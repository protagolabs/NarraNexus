"""
@file_name: _ownership.py
@author:
@date: 2026-08-10
@description: Canonical agent-ownership verification for backend routes.

Consolidates the `_verify_agent_ownership` / `_require_agent_owner` copies that
were pasted into every channel route (home_assistant, slack, lark, telegram,
discord, wechat, narramessenger). Duplicated ownership logic is exactly how
ownership semantics drift apart — the same class of bug that
`AgentRepository.resolve_owner` already fixed on the agent side; this is its
backend-route counterpart.

Ownership = ``agents.created_by``. Local mode (no ``request.state.user_id``,
set by the auth middleware) skips enforcement, preserving the per-route
convention every copy already had.

Two surfaces because the callers historically returned two shapes:
- ``check_owned`` -> error string | None  (channel routes wrap it in a
  ``{"success": False, "error": ...}`` payload)
- ``assert_owned`` -> raises HTTPException  (home_assistant-style routes)
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from xyz_agent_context.repository import AgentRepository
from xyz_agent_context.utils.db.db_factory import get_db_client


def _caller_user_id(request: Request) -> Optional[str]:
    """The authenticated caller, or None in local mode."""
    uid = getattr(request.state, "user_id", None)
    return uid or None


async def check_owned(request: Request, agent_id: str) -> Optional[str]:
    """Return an error string when the caller does not own ``agent_id``.

    Returns None when the caller owns it OR when running in local mode (no
    user_id → no enforcement, as every copy did). A missing/unresolvable
    owner denies access (fail-closed) rather than leaking the resource.
    """
    user_id = _caller_user_id(request)
    if not user_id:
        return None  # local mode — no auth enforcement
    db = await get_db_client()
    owner = await AgentRepository(db).resolve_owner(agent_id)
    if not owner:
        return f"Agent {agent_id} not found."
    if owner != user_id:
        return "Permission denied: you do not own this agent."
    return None


async def assert_owned(request: Request, agent_id: str) -> None:
    """Raise HTTPException(404/403) when the caller does not own ``agent_id``.

    Local mode → no-op. 404 for an unknown agent, 403 for a non-owner.
    """
    err = await check_owned(request, agent_id)
    if err is None:
        return
    raise HTTPException(status_code=404 if "not found" in err else 403, detail=err)
