"""
@file_name: diagnostics.py
@author: NexusAgent
@date: 2026-05-25
@description: Diagnostics surface for Manyfold operators — container
health plus the per-agent pull channel.

Two generations live here:

1. ``GET /manyfold/diagnostics`` (2026-05-25, spec Part 6.7): one curl
   shows whether the container is healthy — DB reachable, env present,
   file system writable, credentials in place.
2. The per-agent PULL channel (2026-08-10, observability plan A): the
   on-demand half of the observability design. The push half (log sink)
   ships what the process chose to emit; these endpoints answer what
   push can't — DB content that never ships (events full text), sink
   gaps, ad-hoc queries — and they work when the sink itself is broken
   (the two paths deliberately share no machinery):

   - ``…/agents/{id}/diagnostics/ingress``   — audit-trail query (the
     managed-ingress lifecycle rows + manyfold files-write rows)
   - ``…/agents/{id}/diagnostics/events``    — run summaries; full
     content only per-id (heavy + sensitive ⇒ explicit pull)
   - ``…/diagnostics/logs/services|tail``    — service-log tail. NOT
     agent-scoped: logs are process-level. Safe here because this
     router only registers under ENABLE_MANYFOLD_API=1 — a sprite is a
     single-user sandbox, so process scope IS user scope.

Registered only when ENABLE_MANYFOLD_API=1. Requires the Manyfold gateway
token. All new endpoints are read-only; known credential shapes are
redacted from every response (cheap insurance, not a formal guarantee).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from xyz_agent_context.utils.db.db_factory import get_db_client
from backend.auth_errors import GATEWAY_TOKEN_INVALID, AuthError


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise AuthError(
            GATEWAY_TOKEN_INVALID,
            "missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


def _claude_credentials_present() -> bool:
    """Look for any of the credential paths claude CLI accepts."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    home = Path(os.environ.get("HOME", "/home/app"))
    creds = home / ".claude" / ".credentials.json"
    return creds.is_file()


async def _db_reachable() -> bool:
    try:
        db = await get_db_client()
        # Cheapest no-op: list 0 users with limit semantics. get_one is OK.
        await db.get_one("users", {})
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[manyfold-diag] DB probe failed: {e}")
        return False


def _frontend_dist_present() -> bool:
    """Mirror backend/main.py logic for SPA fallback existence."""
    try:
        from backend.config import settings
        dist = settings.frontend_dist
        return dist.is_dir() and (dist / "index.html").exists()
    except Exception:  # noqa: BLE001
        return False


def _writable(path: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    return os.access(p, os.W_OK)


@router.get("/manyfold/diagnostics")
async def diagnostics(request: Request):
    _require_manyfold_auth(request)

    checks: dict[str, Any] = {
        "claude_cli_installed": shutil.which("claude") is not None,
        "claude_credentials_configured": _claude_credentials_present(),
        "frontend_dist_present": _frontend_dist_present(),
        "gateway_token_set": bool(os.environ.get("MANYFOLD_GATEWAY_TOKEN")),
        "writable_data_dir": _writable(os.environ.get("BASE_WORKING_PATH", "/data")) or _writable("/data"),
        "writable_claude_dir": _writable(
            str(Path(os.environ.get("HOME", "/home/app")) / ".claude")
        ),
        "db_reachable": await _db_reachable(),
    }

    image_version = os.environ.get("IMAGE_VERSION", "unknown")
    warnings: list[str] = []
    for k, v in checks.items():
        if not v:
            warnings.append(f"check failed: {k}")

    return {
        "image_version": image_version,
        "manyfold_api_enabled": True,
        "checks": checks,
        "warnings": warnings,
        "all_ok": not warnings,
    }


# ---------------------------------------------------------------------------
# Per-agent pull channel (observability plan A, 2026-08-10)
# ---------------------------------------------------------------------------

# Response caps. `limit` bounds row counts; the per-field cap stops one
# MEDIUMTEXT column (event_log can reach tens of MB) from blowing the
# response past what a diagnostic client wants to page through.
_MAX_ROWS = 200
_DEFAULT_ROWS = 50
_MAX_FIELD_BYTES = 512 * 1024
_MAX_TAIL_LINES = 2000

# Known credential shapes, redacted from every response body. Two forms:
# JSON-ish `"key": "value"` pairs (audit details / env_context) and
# bearer headers that leaked into log lines. Insurance against the known
# offenders, not a formal no-secrets guarantee.
_CREDENTIAL_KEY_RE = re.compile(
    r'("(?:context_token|reply_token|bot_token|app_secret|app_token|'
    r'access_token|matrix_access_token|api_key|gateway_token)"\s*:\s*")'
    r'[^"]*(")'
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}")


def _redact(text: str) -> str:
    text = _CREDENTIAL_KEY_RE.sub(r"\1[redacted]\2", text)
    return _BEARER_RE.sub(r"\1[redacted]", text)


def _clip(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) > _MAX_FIELD_BYTES:
        return (
            text[:_MAX_FIELD_BYTES]
            + f"\n…[truncated {len(text) - _MAX_FIELD_BYTES} chars]"
        )
    return text


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_ROWS))


def _time_str(value: Any) -> str:
    """Uniform sortable string for a DATETIME cell (sqlite returns
    datetime objects, mysql returns strings)."""
    return str(value or "")


@router.get("/manyfold/agents/{agent_id}/diagnostics/ingress")
async def agent_ingress_audit(
    agent_id: str,
    request: Request,
    since: Optional[str] = Query(None, description="ISO lower bound on event_time"),
    event_type: Optional[str] = Query(None),
    limit: int = Query(_DEFAULT_ROWS, description=f"max {_MAX_ROWS}"),
):
    """Audit-trail slice for one agent, newest first.

    The first consumer of the managed-ingress audit events (#267):
    denied / silent / attachments / processed rows plus this agent's
    manyfold files-write rows — "what happened to my message" as one
    query instead of a probe session.
    """
    _require_manyfold_auth(request)
    db = await get_db_client()
    filters: dict[str, Any] = {"agent_id": agent_id}
    if event_type:
        filters["event_type"] = event_type
    rows = await db.get("channel_trigger_audit", filters)
    rows.sort(key=lambda r: _time_str(r.get("event_time")), reverse=True)
    if since:
        rows = [r for r in rows if _time_str(r.get("event_time")) >= since]
    out = []
    for r in rows[: _clamp_limit(limit)]:
        details_raw = _redact(str(r.get("details") or "{}"))
        try:
            details = json.loads(details_raw)
        except ValueError:
            details = {"_raw": details_raw[:1000]}
        out.append(
            {
                "event_time": _time_str(r.get("event_time")),
                "event_type": r.get("event_type"),
                "channel": r.get("channel"),
                "message_id": r.get("message_id"),
                "chat_id": r.get("chat_id"),
                "sender_id": r.get("sender_id"),
                "details": details,
            }
        )
    return {"data": out, "object": "list"}


@router.get("/manyfold/agents/{agent_id}/diagnostics/events")
async def agent_events_summary(
    agent_id: str,
    request: Request,
    since: Optional[str] = Query(None, description="ISO lower bound on started_at"),
    limit: int = Query(_DEFAULT_ROWS, description=f"max {_MAX_ROWS}"),
):
    """Run summaries, newest first — deliberately WITHOUT content fields.

    The list answers "which runs happened and how did they end"; the
    heavy, user-content-bearing columns (env_context / final_output /
    event_log) are only served per-id by the endpoint below, keeping
    bulk content out of casual queries (DB is pull-only by policy).
    """
    _require_manyfold_auth(request)
    db = await get_db_client()
    rows = await db.get("events", {"agent_id": agent_id})
    rows.sort(key=lambda r: _time_str(r.get("started_at")), reverse=True)
    if since:
        rows = [r for r in rows if _time_str(r.get("started_at")) >= since]
    out = [
        {
            "event_id": r.get("event_id"),
            "trigger": r.get("trigger"),
            "trigger_source": r.get("trigger_source"),
            "state": r.get("state"),
            "started_at": _time_str(r.get("started_at")),
            "finished_at": _time_str(r.get("finished_at")),
            "tool_call_count": r.get("tool_call_count"),
            "current_stage": r.get("current_stage"),
            "error_message": _clip(r.get("error_message"))[:500] or None,
            "narrative_id": r.get("narrative_id"),
            "sizes": {
                "env_context": len(str(r.get("env_context") or "")),
                "final_output": len(str(r.get("final_output") or "")),
                "event_log": len(str(r.get("event_log") or "")),
            },
        }
        for r in rows[: _clamp_limit(limit)]
    ]
    return {"data": out, "object": "list"}


@router.get("/manyfold/agents/{agent_id}/diagnostics/events/{event_id}")
async def agent_event_full(agent_id: str, event_id: str, request: Request):
    """One run's full record — input, output, tool log.

    Scoped WHERE agent_id AND event_id: an event belonging to another
    agent 404s identically to a missing one (no existence oracle).
    Credential shapes are redacted; oversized fields are clipped with an
    explicit truncation marker.
    """
    _require_manyfold_auth(request)
    db = await get_db_client()
    row = await db.get_one("events", {"agent_id": agent_id, "event_id": event_id})
    if not row:
        raise HTTPException(status_code=404, detail="no such event for this agent")
    return {
        "event_id": row.get("event_id"),
        "trigger": row.get("trigger"),
        "trigger_source": row.get("trigger_source"),
        "state": row.get("state"),
        "started_at": _time_str(row.get("started_at")),
        "finished_at": _time_str(row.get("finished_at")),
        "tool_call_count": row.get("tool_call_count"),
        "current_stage": row.get("current_stage"),
        "error_message": _redact(_clip(row.get("error_message"))) or None,
        "narrative_id": row.get("narrative_id"),
        "env_context": _redact(_clip(row.get("env_context"))),
        "final_output": _redact(_clip(row.get("final_output"))),
        "event_log": _redact(_clip(row.get("event_log"))),
        "module_instances": _redact(_clip(row.get("module_instances"))),
    }


@router.get("/manyfold/diagnostics/logs/services")
async def logs_services(request: Request):
    """Which service logs exist in this sandbox, with their dates."""
    _require_manyfold_auth(request)
    from backend.routes.admin.logs import _list_files, _log_root

    root = _log_root()
    services: dict[str, Any] = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                services[d.name] = [f["name"] for f in _list_files(d)]
    return {"log_root": str(root), "services": services}


@router.get("/manyfold/diagnostics/logs/tail")
async def logs_tail(
    request: Request,
    service: str = Query(...),
    lines: int = Query(200, description=f"max {_MAX_TAIL_LINES}"),
    date: Optional[str] = Query(None, description="YYYYMMDD; default today"),
    level: Optional[str] = Query(None),
    grep: Optional[str] = Query(None, max_length=200, description="plain substring"),
):
    """Tail one service's log without shelling into the sandbox.

    Reuses the admin logs read helpers (file resolution / seek-tail /
    level filter) — read logic shared, auth deliberately NOT: this stays
    on the gateway token, /api/admin keeps its session auth. ``grep`` is
    a plain substring match, not a regex (no ReDoS surface).
    """
    _require_manyfold_auth(request)
    from backend.routes.admin.logs import (
        _filter_by_level,
        _resolve_log_path,
        _resolve_service_dir,
        _tail_lines,
    )

    lines = max(1, min(lines, _MAX_TAIL_LINES))
    service_dir = _resolve_service_dir(service)
    path = _resolve_log_path(service_dir, service, date)
    tail = _filter_by_level(_tail_lines(path, lines), level)
    if grep:
        tail = [ln for ln in tail if grep in ln]
    return {
        "service": service,
        "file": path.name,
        "lines": [_redact(ln) for ln in tail[-lines:]],
    }
