"""
@file_name: _narramessenger_mcp_tools.py
@date: 2026-07-02
@description: NarraMessenger MCP tools — the agent-facing reply/send/bind surface.

Tools exposed:
  - narra_reply(agent_id, invocation_id, text) — reply to the message you were
    invoked on. Goes through ``/invocations/{id}/reply``: delivers AND closes
    the invocation (so the platform does NOT fire the 15-min timeout). This is
    the reply path; ``invocation_id`` is given to you in the turn's context.
  - narra_send(agent_id, room_id, text)        — PROACTIVE send to a room via
    ``/chat/send`` (no invocation to close, no time limit). Use this when you
    are NOT replying to an inbound message (e.g. a Job/scheduled push).
  - narra_bind(agent_id, bind_command)         — bind this agent to NarraMessenger
    from a pasted bind link (drives the Gateway bind + writes the credential).
  - narra_status(agent_id)                     — sanitised binding status + live
    ``/status`` check.
  - narra_room_members(agent_id, room_id)      — live roster fetch via
    ``GET /_matrix/client/v3/rooms/{room_id}/joined_members``. Kept as a
    tool (not baked into prompt) so the agent only pays for the roster
    when it actually needs to know "who's in this room" — the vast
    majority of turns don't. Added 2026-07-02 alongside the Direct
    Matrix migration.
"""

from __future__ import annotations

import uuid
from typing import Any

import aiohttp
from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule

from ._narramessenger_client import NarramessengerAPIError, NarramessengerClient
from ._narramessenger_credential_manager import NarramessengerCredentialManager
from ._narramessenger_service import do_bind


async def _get_credential(agent_id: str):
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = NarramessengerCredentialManager(db)
    return await mgr.get(agent_id)


def register_narramessenger_mcp_tools(mcp: Any) -> None:
    """Register NarraMessenger MCP tools on the given FastMCP server."""

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_reply(agent_id: str, invocation_id: str, text: str) -> dict:
        """Reply to the NarraMessenger message you were invoked on.

        ``invocation_id`` is provided in this turn's context (the identity
        block). ``text`` is your real, user-facing answer in plain text.

        This delivers your message AND closes the invocation, so the sender
        does NOT see a "timed out" error. Use this to REPLY. For proactive
        sends (not in response to a message), use ``narra_send`` instead.

        Returns ``{"ok": true}`` on success, else ``{"ok": false, "error": ...}``.
        """
        if not invocation_id or not text or not text.strip():
            return {"ok": False, "error": "invocation_id and non-empty text are required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}

        client = NarramessengerClient(cred.bearer_token, cred.backend_base_url)
        try:
            await client.reply(invocation_id=invocation_id, text=text)
            return {"ok": True}
        except NarramessengerAPIError as e:
            return {"ok": False, "error": e.code, "status": e.status}
        finally:
            await client.close()

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_send(agent_id: str, room_id: str, text: str) -> dict:
        """PROACTIVELY send a message to a NarraMessenger room (NOT a reply).

        ``room_id`` is a Matrix room id (e.g. ``!abc:matrix.netmind.chat``).
        Use this only when you are sending on your own behalf — e.g. from a
        Job, a scheduled task, or following up after finishing long work —
        NOT when replying to a message you were invoked on (use ``narra_reply``
        for that, so the invocation gets closed).

        Returns ``{"ok": true, "event_id": ...}`` on success.
        """
        if not room_id or not text or not text.strip():
            return {"ok": False, "error": "room_id and non-empty text are required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}

        client = NarramessengerClient(cred.bearer_token, cred.backend_base_url)
        try:
            data = await client.chat_send(
                room_id=room_id, text=text, txn_id=str(uuid.uuid4())
            )
            return {"ok": True, "event_id": data.get("event_id")}
        except NarramessengerAPIError as e:
            return {"ok": False, "error": e.code, "status": e.status}
        finally:
            await client.close()

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_bind(agent_id: str, bind_command: str) -> dict:
        """Bind this agent to NarraMessenger from a pasted bind command/link.

        The owner gets the bind command from the NarraMessenger app
        (My Space → My Agents → Bind Agents) and pastes it. Pass that whole
        string here; this drives the Gateway bind and saves the credential.
        Once it returns success, real-time receiving starts automatically.

        Returns ``{"success": true, "data": {...}}`` or
        ``{"success": false, "error": ...}``.
        """
        if not bind_command or not bind_command.strip():
            return {"success": False, "error": "bind_command is required"}
        db = await XYZBaseModule.get_mcp_db_client()
        return await do_bind(db, agent_id, bind_command)

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_status(agent_id: str) -> dict:
        """Return sanitised NarraMessenger binding status (NO bearer token),
        plus a live ``/status`` check of the backend."""
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": True, "data": None, "bound": False}

        live: dict[str, Any]
        client = NarramessengerClient(cred.bearer_token, cred.backend_base_url)
        try:
            live = await client.status()
        except NarramessengerAPIError as e:
            live = {"error": e.code, "status": e.status}
        finally:
            await client.close()

        public = cred.to_public_dict()
        public["bound"] = True
        public["live_check"] = live
        return {"success": True, "data": public}

    # ──────────────────────────────────────────────────────────────────
    @mcp.tool()
    async def narra_room_members(agent_id: str, room_id: str) -> dict:
        """List the joined members of a NarraMessenger room.

        Live GET to the Matrix homeserver's
        ``/_matrix/client/v3/rooms/{room_id}/joined_members`` endpoint,
        using the Matrix access token stored on the credential (NOT the
        Narra bearer — Matrix rejects the Narra bearer with
        ``M_UNKNOWN_TOKEN``).

        Use this when you need to know WHO is in a group room — e.g. to
        @-mention someone specific, to answer "who's in this room?", or
        to plan a message to a particular subset of members. This is
        NOT auto-injected into every turn's prompt (would cost too many
        tokens on large groups); the agent calls it on demand.

        Returns:
            {"ok": true, "members": [
                {"user_id": "@alice:h", "display_name": "Alice",
                 "avatar_url": "mxc://..."},
                ...
            ]}
            or {"ok": false, "error": <code>}.
        """
        if not room_id:
            return {"ok": False, "error": "room_id is required"}

        cred = await _get_credential(agent_id)
        if not cred:
            return {"ok": False, "error": "no_credential",
                    "hint": "no NarraMessenger binding for this agent"}
        if not cred.matrix_access_token or not cred.matrix_homeserver_url:
            return {"ok": False, "error": "no_matrix_credentials",
                    "hint": "credential is not on the Matrix transport"}

        url = (
            f"{cred.matrix_homeserver_url.rstrip('/')}"
            f"/_matrix/client/v3/rooms/{room_id}/joined_members"
        )
        headers = {"Authorization": f"Bearer {cred.matrix_access_token}"}
        timeout = aiohttp.ClientTimeout(total=10.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status < 200 or resp.status >= 300:
                        # Matrix errors surface with a JSON body carrying
                        # `errcode`; propagate for observability.
                        try:
                            body = await resp.json()
                        except Exception:  # noqa: BLE001
                            body = {}
                        return {
                            "ok": False,
                            "error": body.get("errcode") or f"http_{resp.status}",
                            "message": body.get("error") or "",
                        }
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            return {"ok": False, "error": "transport_error",
                    "message": f"{type(e).__name__}: {e}"}

        # Matrix returns {"joined": {mxid: {display_name, avatar_url}}}
        joined = data.get("joined") or {}
        members = [
            {
                "user_id": mxid,
                "display_name": info.get("display_name") or mxid,
                "avatar_url": info.get("avatar_url") or "",
            }
            for mxid, info in joined.items()
        ]
        return {"ok": True, "members": members, "count": len(members)}

    logger.info(
        "NarraMessenger MCP tools registered: "
        "narra_reply, narra_send, narra_bind, narra_status, "
        "narra_room_members"
    )
