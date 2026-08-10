"""
@file_name: _ownership.py
@author:
@date: 2026-08-10
@description: Canonical agent-ownership verification for backend routes.

Consolidates the `_verify_agent_ownership` / `_require_agent_owner` copies
this refactor has absorbed so far (home_assistant, slack, lark, telegram,
discord, wechat, narramessenger — further same-shape copies in
agents/artifacts, agents/llm_config, agents/circuit_breaker and migrate.py
are tracked as follow-ups, each with semantics that drifted and need their
own judgement). Duplicated ownership logic is exactly how ownership
semantics drift apart — the same class of bug that
`AgentRepository.resolve_owner` already fixed on the agent side; this is its
backend-route counterpart.

Ownership = ``agents.created_by``.

SECURITY POSTURE — read before hanging anything sensitive off this helper:
when the backend runs without the auth middleware enforcing identity (local
mode: no ``request.state.user_id``), enforcement is SKIPPED — every route
using this helper is then effectively unauthenticated, and any HTTP caller
that can reach the backend port can bind / unbind / test any bot. Do NOT
add sensitive operations behind this helper assuming auth — they won't have
any in local mode. All IM-channel routes mirror this exact contract; keep
them in lockstep.

Two surfaces because the callers historically returned two shapes:
- ``check_owned`` -> error string | None  (channel routes wrap it in a
  ``{"success": False, "error": ...}`` payload)
- ``assert_owned`` -> raises HTTPException  (home_assistant-style routes)

Both map from one shared deny-reason decision, so neither surface ever
parses the other's prose to learn what happened.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from xyz_agent_context.repository import AgentRepository
from xyz_agent_context.utils.db.db_factory import get_db_client

# Deny reasons — the ONE decision both public surfaces map from.
_DENY_UNKNOWN = "unknown"      # agent does not exist -> 404-class
_DENY_NOT_OWNER = "not_owner"  # exists, not yours -> 403-class
_DENY_DB_ERROR = "db_error"    # the LOOKUP failed -> 5xx-class, never "not found"


def _caller_user_id(request: Request) -> Optional[str]:
    """The authenticated caller, or None in local mode."""
    uid = getattr(request.state, "user_id", None)
    return uid or None


async def _deny_reason(request: Request, agent_id: str) -> Optional[str]:
    """Why the caller may NOT touch ``agent_id`` — or None to allow.

    Local mode (no user_id) allows: see the module docstring's security
    posture. A failed owner LOOKUP is its own reason (PR #258 review #4):
    ``resolve_owner`` distinguishes ``None`` (db failure) from ``""``
    (unknown agent), and reporting an infrastructure failure as "not found"
    would make a db outage look like a batch of users' agents vanishing,
    with no 5xx metric to alarm on.
    """
    user_id = _caller_user_id(request)
    if not user_id:
        return None  # local mode — no auth enforcement
    db = await get_db_client()
    owner = await AgentRepository(db).resolve_owner(agent_id)
    if owner is None:
        return _DENY_DB_ERROR
    if not owner:
        return _DENY_UNKNOWN
    if owner != user_id:
        return _DENY_NOT_OWNER
    return None


async def check_owned(request: Request, agent_id: str) -> Optional[str]:
    """Return an error string when the caller does not own ``agent_id``.

    Returns None when the caller owns it OR when running in local mode (no
    user_id → no enforcement, as every copy did). Unknown/unresolvable
    owners deny (fail-closed) rather than leak the resource.
    """
    reason = await _deny_reason(request, agent_id)
    if reason is None:
        return None
    if reason == _DENY_DB_ERROR:
        return "Ownership check unavailable (database error) — try again."
    if reason == _DENY_UNKNOWN:
        return f"Agent {agent_id} not found."
    return "Permission denied: you do not own this agent."


async def assert_owned(request: Request, agent_id: str) -> None:
    """Raise HTTPException when the caller does not own ``agent_id``.

    Local mode → no-op. 404 unknown agent, 403 non-owner, 503 when the
    ownership lookup itself failed (an infrastructure fault must surface as
    a server error, not as the resource's absence).
    """
    reason = await _deny_reason(request, agent_id)
    if reason is None:
        return
    if reason == _DENY_DB_ERROR:
        raise HTTPException(
            status_code=503,
            detail="Ownership check unavailable (database error) — try again.",
        )
    if reason == _DENY_UNKNOWN:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found.")
    raise HTTPException(
        status_code=403, detail="Permission denied: you do not own this agent."
    )
