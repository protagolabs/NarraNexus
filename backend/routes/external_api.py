"""
@file_name: external_api.py
@author: NarraNexus
@date: 2026-06-11
@description: /v1/external/* route surface for the external API protocol (v0.3).

Routes registered here:
  - GET    /v1/external/healthz                    (no auth — probe)
  - POST   /v1/external/chat/completions           (Step 6 — chat)
  - GET    /v1/external/agents/{aid}/sessions      (Step 7 — list)
  - DELETE /v1/external/agents/{aid}/sessions/{sid} (Step 7 — delete)

Auth is handled by the middleware in backend/auth.py: every non-/healthz
request must carry a valid nxk_ token. By the time a handler runs:
  - request.state.external_api_authed = True
  - request.state.api_key_agent_id    = the agent the token is scoped to
  - request.state.api_key_owner_user_id = the agent owner
  - request.state.api_key_scopes        = list of allowed scopes

Steps 6 + 7 fill in the placeholders; this file registers the path
prefix so the middleware actually gets hit.

This entire router is conditionally registered (see backend/main.py):
when ENABLE_EXTERNAL_API is unset, the routes are not bound and the
path returns FastAPI's default 404 — same behaviour as if the file
weren't imported.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request


router = APIRouter()


@router.get("/v1/external/healthz")
async def external_healthz():
    """Unauthenticated readiness probe.

    Always returns 200 once the FastAPI app is up. Does NOT touch the
    database — Kubernetes calls this every few seconds and a DB outage
    shouldn't flap pod readiness (we'd return 503 from real handlers
    instead).
    """
    return {"status": "ok", "service": "narranexus-external-api"}


@router.post("/v1/external/chat/completions")
async def external_chat_completions(request: Request):
    """Placeholder — filled in Step 6 of the v0.3 implementation.

    Returns 501 Not Implemented so integrators wiring against this in
    parallel see a meaningful error instead of guessing what's wrong.
    The middleware has already authed by the time we reach this point,
    so `request.state.api_key_agent_id` is available — the placeholder
    confirms it as a smoke check.
    """
    agent_id = getattr(request.state, "api_key_agent_id", None)
    raise HTTPException(
        status_code=501,
        detail={
            "code": "not_implemented",
            "message": (
                "/v1/external/chat/completions will land in Step 6 of "
                "the v0.3 implementation. The auth chain is wired and "
                "your token is valid — token scoped to agent_id={}.".format(
                    agent_id
                )
            ),
        },
    )


@router.get("/v1/external/agents/{agent_id}/sessions")
async def external_list_sessions(agent_id: str, request: Request):
    """Placeholder — filled in Step 7."""
    raise HTTPException(
        status_code=501,
        detail={
            "code": "not_implemented",
            "message": "GET /v1/external/agents/{id}/sessions lands in Step 7",
        },
    )


@router.delete("/v1/external/agents/{agent_id}/sessions/{session_id}")
async def external_delete_session(agent_id: str, session_id: str, request: Request):
    """Placeholder — filled in Step 7."""
    raise HTTPException(
        status_code=501,
        detail={
            "code": "not_implemented",
            "message": "DELETE /v1/external/agents/{id}/sessions/{sid} lands in Step 7",
        },
    )
