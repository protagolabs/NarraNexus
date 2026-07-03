"""
@file_name: _matrix_send.py
@date: 2026-07-03
@description: Matrix-native OUTBOUND helpers — upload media + room_send.

Used by the NarraMessenger MCP tools (``narra_send`` text, ``narra_send_media``
media). These run in the MCP server process, which has NO live matrix-nio
client (that lives in the trigger process), so everything here is raw
authenticated HTTP against the homeserver using the agent's
``matrix_access_token`` — the same pattern as ``narra_room_members``.

Why outbound is Matrix-native (not the Gateway ``/chat/send``): the whole
transport is Matrix now (Commit 7 deleted the Gateway trigger). Sending
text via ``/chat/send`` while receiving via ``/sync`` was a split-brain
left over from the Gateway era, and ``/chat/send`` can carry neither media
nor (future) progressive ``m.replace`` edits. Standardising every send on
``room_send`` unblocks both media and streaming.

Endpoints:
  - upload:    POST {homeserver}/_matrix/media/v3/upload?filename=<name>
               (media upload has always been authenticated; only *download*
               moved to the /client/v1 authenticated path in MSC3916)
  - room_send: PUT  {homeserver}/_matrix/client/v3/rooms/{room}/send/
               m.room.message/{txn}
"""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Any, Optional

import aiohttp
from loguru import logger

from xyz_agent_context.utils.workspace_paths import agent_workspace_path

_UPLOAD_PATH = "/_matrix/media/v3/upload"
_ROOM_SEND_PATH = "/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=60)


class MatrixSendError(Exception):
    """Raised by the send helpers. ``code`` lets the MCP tool return a
    structured error the agent can reason about (bad_path / not_found /
    oversized / http_error / client_error)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def msgtype_for_mime(mime_type: str) -> str:
    """Map a MIME type to the Matrix ``msgtype`` for an ``m.room.message``.

    Coarse by design (image/audio/video/file) — mirrors the receive side's
    content_type derivation so send and receive agree.
    """
    mime = (mime_type or "").lower()
    if mime.startswith("image/"):
        return "m.image"
    if mime.startswith("audio/"):
        return "m.audio"
    if mime.startswith("video/"):
        return "m.video"
    return "m.file"


def resolve_workspace_file(
    agent_id: str, owner_id: str, file_path: str
) -> Path:
    """Resolve ``file_path`` to an absolute path CONFINED to the agent's
    workspace, so ``narra_send_media`` can only ship files the agent owns.

    The agent may pass a path relative to its workspace root or an absolute
    path inside it. Anything that resolves outside the workspace (``..``
    traversal, an absolute path elsewhere) raises ``MatrixSendError`` —
    this is the single security gate on outbound files.
    """
    if not file_path or not file_path.strip():
        raise MatrixSendError("bad_path", "file_path is empty")

    root = agent_workspace_path(agent_id, owner_id).resolve()
    # ``root / abs_path`` yields ``abs_path``; ``resolve()`` also collapses
    # any ``..`` — so the relative_to check below is the real containment
    # guarantee regardless of whether the agent passed relative or absolute.
    target = (root / file_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise MatrixSendError(
            "bad_path",
            f"file_path escapes the agent workspace: {file_path}",
        ) from e
    if not target.is_file():
        raise MatrixSendError("not_found", f"no such file: {file_path}")
    return target


async def matrix_upload(
    *,
    homeserver: str,
    token: str,
    filename: str,
    mime_type: str,
    data: bytes,
) -> str:
    """Upload bytes to the homeserver's media repo, return the ``mxc://`` URI.

    Raises ``MatrixSendError`` on HTTP / transport error or a missing
    ``content_uri`` in the response.
    """
    base = (homeserver or "").rstrip("/")
    if not base or not token:
        raise MatrixSendError("no_credential", "missing homeserver/token")

    url = f"{base}{_UPLOAD_PATH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": mime_type or "application/octet-stream",
    }
    params = {"filename": filename} if filename else None
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(
                url, headers=headers, params=params, data=data
            ) as resp:
                if resp.status != 200:
                    raise MatrixSendError(
                        "http_error", f"upload status {resp.status}"
                    )
                body = await resp.json()
    except MatrixSendError:
        raise
    except (aiohttp.ClientError, TimeoutError) as e:
        raise MatrixSendError("client_error", f"{type(e).__name__}: {e}") from e

    mxc = (body or {}).get("content_uri") or ""
    if not mxc:
        raise MatrixSendError("http_error", "upload response missing content_uri")
    return mxc


async def matrix_room_send(
    *,
    homeserver: str,
    token: str,
    room_id: str,
    content: dict,
    txn_id: Optional[str] = None,
) -> str:
    """Send one ``m.room.message`` event, return its ``event_id``.

    Raises ``MatrixSendError`` on HTTP / transport error.
    """
    base = (homeserver or "").rstrip("/")
    if not base or not token:
        raise MatrixSendError("no_credential", "missing homeserver/token")
    if not room_id:
        raise MatrixSendError("bad_room", "room_id is required")

    txn = txn_id or f"nx-{uuid.uuid4().hex}"
    url = base + _ROOM_SEND_PATH.format(room_id=room_id, txn_id=txn)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.put(url, headers=headers, json=content) as resp:
                if resp.status != 200:
                    try:
                        err = await resp.json()
                    except Exception:  # noqa: BLE001
                        err = {}
                    raise MatrixSendError(
                        err.get("errcode") or f"http_{resp.status}",
                        err.get("error") or f"room_send status {resp.status}",
                    )
                body = await resp.json()
    except MatrixSendError:
        raise
    except (aiohttp.ClientError, TimeoutError) as e:
        raise MatrixSendError("client_error", f"{type(e).__name__}: {e}") from e

    return (body or {}).get("event_id") or ""


async def send_media_impl(
    *,
    agent_id: str,
    owner_id: str,
    homeserver: str,
    token: str,
    room_id: str,
    file_path: str,
    max_bytes: int,
    caption: Optional[str] = None,
) -> dict[str, Any]:
    """Ship one workspace file to a Matrix room as an ``m.image`` / ``m.file``
    / ``m.audio`` / ``m.video`` event.

    Never raises — returns ``{"ok": True, "event_id", "mxc"}`` or
    ``{"ok": False, "error", ...}`` so the MCP tool surfaces a clean result
    the agent can act on.
    """
    try:
        target = resolve_workspace_file(agent_id, owner_id, file_path)
    except MatrixSendError as e:
        return {"ok": False, "error": e.code, "message": str(e)}

    data = target.read_bytes()
    if max_bytes and len(data) > max_bytes:
        return {
            "ok": False,
            "error": "oversized",
            "message": f"file is {len(data)} bytes > cap {max_bytes}",
        }

    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    msgtype = msgtype_for_mime(mime_type)

    # body = the alt-text / display name. When a caption is given we surface
    # it as the body (what clients show) and keep the real filename in the
    # MSC2530 ``filename`` field; otherwise body IS the filename.
    body = caption if (caption and caption.strip()) else target.name
    content: dict[str, Any] = {
        "msgtype": msgtype,
        "body": body,
        "filename": target.name,
        "url": "",  # filled after upload
        "info": {"mimetype": mime_type, "size": len(data)},
    }

    try:
        mxc = await matrix_upload(
            homeserver=homeserver,
            token=token,
            filename=target.name,
            mime_type=mime_type,
            data=data,
        )
        content["url"] = mxc
        event_id = await matrix_room_send(
            homeserver=homeserver, token=token, room_id=room_id, content=content
        )
    except MatrixSendError as e:
        logger.warning(
            f"[narramessenger:{agent_id}] send_media failed "
            f"({e.code}) room={room_id} file={target.name}: {e}"
        )
        return {"ok": False, "error": e.code, "message": str(e)}

    logger.info(
        f"[narramessenger:{agent_id}] sent {msgtype} to {room_id} "
        f"(file={target.name}, {len(data)} bytes, event={event_id})"
    )
    return {"ok": True, "event_id": event_id, "mxc": content["url"], "msgtype": msgtype}
