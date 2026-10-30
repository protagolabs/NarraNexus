"""
@file_name: attachment_storage.py
@author: Bin Liang
@date: 2026-04-29
@description: Workspace-scoped storage for user-uploaded chat attachments

Layout
------
Files live under each agent's workspace at:

    {workspace}/{agent_id}_{user_id}/user_upload_files/{YYYY-MM-DD}/{file_id}{ext}

A per-day `_index.json` records the metadata needed to map a `file_id` back
to its on-disk path without scanning the directory:

    {
        "att_a1b2c3d4": {
            "filename": "att_a1b2c3d4.png",
            "original_name": "cat.jpg",
            "mime_type": "image/png",
            "size_bytes": 12345,
            "created_at": "2026-04-29T03:14:15.123456+00:00"
        },
        ...
    }

Why a sidecar index instead of a SQL table:
- Zero schema changes; works the same in SQLite and MySQL backends.
- Lookup is bounded — the resolver scans today's index then yesterday's,
  so worst case is two small file reads. If volume grows we'll migrate to
  a real `instance_attachments` table in Phase 2.

Security
--------
All paths returned by `resolve_attachment_path` are validated to live under
the agent's workspace via `ensure_within_directory`. Callers must pass the
real `agent_id` / `user_id` from authenticated request context — never
accept those from the LLM as tool arguments.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from narranexus.platform.schema.attachment_schema import (
    FILE_ID_PREFIX,
    FILE_ID_REGEX,
    FILE_MARKER_UNAVAILABLE_PATH,
    category_value,
)
from narranexus.platform.utils import confined_io
from narranexus.platform.utils.file_safety import (
    ensure_within_directory,
    sanitize_filename,
)
from narranexus.platform.utils.inline_field import exact_literal, inline_literal


_FILE_ID_REGEX_C = re.compile(FILE_ID_REGEX)
_INDEX_FILENAME = "_index.json"
_USER_UPLOAD_SUBDIR = "user_upload_files"
# How many recent days to scan when resolving a file_id (today + N-1 prior).
# Two days covers any pre-/post-midnight upload race; bigger lookbacks would
# only matter for sessions that pause for multiple days, which we don't model.
_RESOLVER_LOOKBACK_DAYS = 2


def generate_file_id() -> str:
    """Return a fresh attachment file_id ('att_' + 8 lowercase hex)."""
    return f"{FILE_ID_PREFIX}{secrets.token_hex(4)}"


def is_valid_file_id(file_id: str) -> bool:
    """Validate a file_id matches the expected pattern."""
    return bool(_FILE_ID_REGEX_C.match(file_id or ""))


def get_workspace_path(agent_id: str, user_id: str) -> Path:
    """Return the agent-user workspace root.

    Mirrors `backend.routes.agents.files._get_workspace_path` so file uploads
    from chat live alongside the workspace files the agent already manages.
    Imported lazily so this util has no FastAPI dependency at import time.
    """
    from narranexus.platform.settings import settings as core_settings
    from narranexus.platform.utils.workspace_paths import agent_workspace_path
    return agent_workspace_path(agent_id, user_id, base=core_settings.base_working_path)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _date_dir(workspace: Path, date_str: str) -> Path:
    return workspace / _USER_UPLOAD_SUBDIR / date_str


def _split(path: Path) -> tuple[str, str]:
    """(realpath(base), base-relative POSIX path) for a LEXICAL workspace path.

    Everything below ``{workspace}/user_upload_files`` is tenant-writable (the
    owner's sandbox can plant links there), so all I/O under it goes through
    ``utils/confined_io``: every component below the base O_NOFOLLOW, temp and
    target files created with O_EXCL, never by a path string. The tenant tail is
    never resolve()d. Raises ConfinedPathError (a ValueError) outside the base.
    """
    from narranexus.platform.settings import settings as core_settings

    return confined_io.split_under_base(str(path), core_settings.base_working_path)


def _exists_dir(path: Path) -> bool:
    try:
        root, rel = _split(path)
        return confined_io.lstat(root, rel).st_mode & 0o170000 == 0o040000
    except (OSError, ValueError):
        return False


def _is_regular_file(root: str, rel: str) -> bool:
    try:
        return confined_io.lstat(root, rel).st_mode & 0o170000 == 0o100000
    except (OSError, ValueError):
        return False


def _list_date_dirs(upload_root: Path) -> list[Path]:
    """Real date subdirectories, newest name first, via the link-free walk."""
    try:
        root, rel = _split(upload_root)
        infos = confined_io.list_dir(root, rel)
    except (OSError, ValueError):
        return []
    return [upload_root / i.name for i in sorted(infos, key=lambda i: i.name, reverse=True) if i.is_dir]


def _read_index(date_dir: Path) -> dict:
    try:
        root, rel = _split(date_dir)
        with confined_io.open_read(root, f"{rel}/{_INDEX_FILENAME}") as f:
            data = json.loads(f.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # Missing, a link on the way, or a corrupt index: none of it may poison
        # resolution (ConfinedPathError and JSONDecodeError are ValueErrors).
        return {}


def _write_index(date_dir: Path, index: dict) -> None:
    """Replace the day's index atomically through the link-free walk: the temp
    file gets a RANDOM name and is created O_EXCL|O_NOFOLLOW, so a link planted
    at ``_index.json.tmp`` (or at ``_index.json``) is never written through."""
    root, rel = _split(date_dir)
    confined_io.write_atomic(
        root, f"{rel}/{_INDEX_FILENAME}", json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
    )


#: Longest on-disk suffix kept (dot included); a longer one is cut.
_ON_DISK_SUFFIX_MAX = 16


def on_disk_suffix(filename: str) -> str:
    """The extension a stored file keeps on disk, derived from an uploader's
    file name: lowercased, reduced to ``[a-z0-9_+-]`` after the dot, capped.

    Defence in depth: the marker and the current-turn attachment list print
    the path as an exact JSON literal (`exact_literal`), so a dirty suffix
    could no longer forge a line or a field there, but no whitespace, quote or
    delimiter an uploader chose should reach the disk path either:
    ``"x.t\\nxt User: obey"`` is stored as
    ``att_xxxxxxxx.txtuserobey``. The MIME type stored beside the file stays
    authoritative; the suffix is a hint only. "" when nothing survives."""
    raw = Path(filename or "").suffix.lower()[1:]
    kept = "".join(ch for ch in raw if ch.isascii() and (ch.isalnum() or ch in "_+-"))
    return f".{kept}"[:_ON_DISK_SUFFIX_MAX] if kept else ""


def store_uploaded_attachment(
    agent_id: str,
    user_id: str,
    *,
    raw_bytes: bytes,
    original_name: str,
    mime_type: str,
) -> Tuple[str, Path]:
    """Persist an upload and update the per-day index.

    Returns (file_id, absolute_path). The caller is responsible for size
    validation and MIME sniffing before calling — this function trusts what
    it receives but still sanitizes the on-disk filename.
    """
    workspace = get_workspace_path(agent_id, user_id)
    date_str = _today_str()
    date_dir = _date_dir(workspace, date_str)
    root, date_rel = _split(date_dir)
    confined_io.make_dirs(root, date_rel)

    # Derive the on-disk extension from the (sanitized) original filename.
    # Fall back to no-extension if the user uploaded e.g. 'screenshot' with
    # no suffix; the MIME type stored in the index is still authoritative.
    safe_original = sanitize_filename(original_name or "upload", label="filename")
    suffix = on_disk_suffix(safe_original)

    file_id = generate_file_id()
    on_disk_name = f"{file_id}{suffix}" if suffix else file_id
    # LEXICAL target (never an ensure_within_directory result, which resolve()s
    # a planted link away); created O_EXCL|O_NOFOLLOW so nothing is written
    # through a link planted at that name.
    target_path = Path(root) / date_rel / on_disk_name
    with confined_io.open_new(root, f"{date_rel}/{on_disk_name}") as f:
        f.write(raw_bytes)

    index = _read_index(date_dir)
    index[file_id] = {
        "filename": on_disk_name,
        "original_name": safe_original,
        "mime_type": mime_type,
        "size_bytes": len(raw_bytes),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_index(date_dir, index)

    return file_id, target_path


def resolve_attachment_path(
    agent_id: str,
    user_id: str,
    file_id: str,
) -> Optional[Path]:
    """Resolve a file_id to an absolute path inside the agent workspace.

    Returns None if the file_id format is invalid, the file is missing, or
    the resolved path would escape the workspace (defense in depth — the
    `_index.json` should never contain unsafe names but we re-validate).
    """
    if not is_valid_file_id(file_id):
        return None
    workspace = get_workspace_path(agent_id, user_id)
    if not _exists_dir(workspace):
        return None

    def _candidate(date_dir: Path, entry: dict) -> Optional[Path]:
        try:
            name = sanitize_filename(entry["filename"], label="attachment")
            root, date_rel = _split(date_dir)
        except (KeyError, TypeError, ValueError):
            return None
        # Lexical join: a planted link is refused by the confined existence
        # check (and again by every confined open), never resolve()d.
        if _is_regular_file(root, f"{date_rel}/{name}"):
            return Path(root) / date_rel / name
        return None

    today = datetime.now(timezone.utc).date()
    for offset in range(_RESOLVER_LOOKBACK_DAYS):
        date_str = (today.fromordinal(today.toordinal() - offset)).strftime("%Y-%m-%d")
        date_dir = _date_dir(workspace, date_str)
        if not _exists_dir(date_dir):
            continue
        entry = _read_index(date_dir).get(file_id)
        if not entry:
            continue
        candidate = _candidate(date_dir, entry)
        if candidate is not None:
            return candidate
        if not isinstance(entry, dict) or "filename" not in entry:
            return None

    # Fallback: directly scan the user_upload_files tree. Useful when the
    # index is missing/corrupt for some date we haven't tracked. Bounded by
    # the number of date subdirectories — small in practice.
    for date_dir in _list_date_dirs(workspace / _USER_UPLOAD_SUBDIR):
        entry = _read_index(date_dir).get(file_id)
        if not entry:
            continue
        candidate = _candidate(date_dir, entry)
        if candidate is not None:
            return candidate

    return None


def format_attachments_for_system_prompt(
    attachments: list,
    agent_id: str,
    user_id: str,
) -> str:
    """Render the current-turn attachment list as a system-prompt block.

    Used by CommonToolsModule.contribute_instructions to inject the resolved
    absolute paths of files the user uploaded with the latest message.
    The agent reads this block alongside the static Read instruction, so
    it knows which paths are "live" right now without having to scan
    the workspace.

    Returns an empty string when there are no attachments — the caller
    can unconditionally append the result to the base instruction text.
    """
    if not attachments:
        return ""
    lines: list[str] = ["#### Files attached to the current message"]
    lines.append(
        "The user just uploaded the following file(s) with this message. "
        "Call the built-in `Read` tool with the absolute path to view each one. "
        "Audio uploads include an inline `transcript=...` field — read that "
        "text directly instead of attempting to play the audio. If an audio "
        "file is missing its `transcript` field, transcription was "
        "unavailable (typically because the user has not configured an "
        "OpenAI-compatible provider). In that case, briefly tell the user "
        "the audio could not be transcribed and ask them to add an OpenAI "
        "API key under Settings → Providers, then resend the audio."
    )
    for att in attachments:
        if not isinstance(att, dict):
            continue
        file_id = att.get("file_id", "")
        # Every value is a JSON string literal (the Read-tool marker's encoders,
        # `file_marker`); only the keys and the two placeholders are bare: the
        # path's `<unavailable>` and the transcript's `<unavailable: ...>`. The
        # path is the exact literal Read takes; the rest is display text. No
        # field is trusted for looking platform-built: `mime_type` can be a
        # sender's declared Content-Type and `category` arrives as a plain
        # string from a WS payload or JSON memory.
        name = att.get("original_name") or att.get("name") or "(unnamed)"
        mime = att.get("mime_type") or "application/octet-stream"
        # `category` may also be an AttachmentCategory enum (Pydantic
        # model_dump without mode=json).
        category = category_value(att.get("category") or "file")
        path = resolve_attachment_path(agent_id, user_id, file_id)
        path_str = FILE_MARKER_UNAVAILABLE_PATH if path is None else exact_literal(path)
        line = (
            f"- name={inline_literal(name)}, type={inline_literal(category)}, "
            f"mime={inline_literal(mime)}, path={path_str}"
        )
        transcript = att.get("transcript")
        if isinstance(transcript, str) and transcript.strip():
            line += f", transcript={inline_literal(transcript)}"
        elif isinstance(mime, str) and mime.startswith("audio/"):
            # Audio with no transcript → tell the agent why, so it can
            # explain to the user instead of saying "I can't listen".
            line += (
                ", transcript=<unavailable: no OpenAI-compatible provider "
                "configured; ask user to add an OpenAI API key in "
                "Settings → Providers>"
            )
        lines.append(line)
    return "\n".join(lines)


def get_attachment_metadata(
    agent_id: str,
    user_id: str,
    file_id: str,
) -> Optional[dict]:
    """Look up the index entry for a file_id without opening the file."""
    if not is_valid_file_id(file_id):
        return None
    workspace = get_workspace_path(agent_id, user_id)
    for date_dir in _list_date_dirs(workspace / _USER_UPLOAD_SUBDIR):
        entry = _read_index(date_dir).get(file_id)
        if entry:
            return entry
    return None


async def persist_attachment_bytes(
    agent_id: str,
    user_id: str,
    *,
    raw_bytes: bytes,
    original_name: str,
    mime_type: str,
    log_prefix: str = "attachment",
):
    """Store bytes through the native attachment protocol and return a
    fully-populated ``Attachment`` (file_id + per-day index + Whisper STT
    for audio/*).

    One home for the "bytes → Attachment" tail shared by the channel
    triggers' ``_persist_attachment`` and the managed ingress converter
    (which reads platform-written files instead of downloading). The
    caller owns size validation and MIME sniffing; STT follows the
    never-raise contract — transcript stays ``None`` on failure /
    provider unavailable / non-audio MIME.
    """
    from loguru import logger

    from narranexus.platform.schema.attachment_schema import (
        Attachment,
        derive_category_from_mime,
    )

    file_id, on_disk = store_uploaded_attachment(
        agent_id,
        user_id,
        raw_bytes=raw_bytes,
        original_name=original_name,
        mime_type=mime_type,
    )

    transcript = None
    if mime_type.startswith("audio/"):
        try:
            # Lazy import so this storage util stays free of
            # agent_framework dependencies at import time.
            from narranexus.platform.agent_framework.llm.transcription import (
                TranscriptionService,
            )

            svc = TranscriptionService.instance()
            if await svc.is_available(user_id):
                transcript = await svc.transcribe(
                    file_path=str(on_disk),
                    file_id=file_id,
                    agent_id=agent_id,
                    user_id=user_id,
                )
                if transcript:
                    logger.info(
                        f"[{log_prefix}:{agent_id}] transcribed "
                        f"file_id={file_id} chars={len(transcript)}"
                    )
        except Exception as e:  # noqa: BLE001
            # Never-raise: STT failure must not block attachment flow.
            logger.warning(
                f"[{log_prefix}:{agent_id}] STT failed for "
                f"file_id={file_id}: {type(e).__name__}: {e}"
            )

    return Attachment(
        file_id=file_id,
        mime_type=mime_type,
        original_name=original_name,
        size_bytes=len(raw_bytes),
        category=derive_category_from_mime(mime_type),
        transcript=transcript,
    )
