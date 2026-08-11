"""
@file_name: narramessenger.py
@date: 2026-06-18
@description: Backend API routes for NarraMessenger binding + status.

Endpoints:
  GET    /api/narramessenger/credential      — sanitised binding info for an agent
  POST   /api/narramessenger/bind            — bind from a pasted bind command/link
  POST   /api/narramessenger/unbind          — remove the binding
  POST   /api/narramessenger/prewarm         — NarraMessenger-called executor prewarm
  GET    /api/narramessenger/prewarm/status  — readiness probe for the prewarm

Bind logic lives in ``_narramessenger_service.do_bind`` (shared with the
``narra_bind`` MCP tool) — these routes are the frontend "paste the bind link"
entry point. The prewarm pair is machine-to-machine: the NarraMessenger
backend calls it with the per-agent bearer_token when a voice call starts
ringing, so the owner's executor container is warm by the time the call
connects.
"""

from __future__ import annotations

import asyncio
import hmac
import time

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from loguru import logger

from xyz_agent_context.agent_framework.loop.broker_client import (
    ExecutorEnsureResult,
    broker_url,
    ensure_executor,
    wait_until_ready,
)
from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredentialManager,
)
from xyz_agent_context.module.narramessenger_module._narramessenger_service import (
    do_bind,
    do_unbind,
)
from xyz_agent_context.repository.agent_repository import AgentRepository


# One canonical owner check (backend/routes/_ownership.py); module-level
# alias keeps the historical local name at its ~per-route call sites. No
# import cycle: this subpackage never gets imported back from _ownership.
from backend.routes._ownership import check_owned as _verify_agent_ownership

router = APIRouter()

_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class BindRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    bind_command: str = Field(min_length=1, max_length=2048)


async def _get_db():
    from xyz_agent_context.utils.db.db_factory import get_db_client
    return await get_db_client()


@router.get("/credential")
async def get_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Sanitised binding info (no bearer token). ``data`` is None if unbound."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    mgr = NarramessengerCredentialManager(db)
    data = await mgr.get_public(agent_id)
    return {"success": True, "data": data}


@router.post("/bind")
async def bind(request: Request, body: BindRequest) -> dict[str, Any]:
    """Bind from a pasted bind command/link (drives the Gateway bind)."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    result = await do_bind(db, body.agent_id, body.bind_command)
    if result.get("success"):
        logger.info(f"NarraMessenger bound: agent={body.agent_id}")
    return result


@router.post("/unbind")
async def unbind(request: Request, body: AgentRequest) -> dict[str, Any]:
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    return await do_unbind(db, body.agent_id)


class PrewarmRequest(BaseModel):
    """Exactly one of the two agent identifiers is required. agent_profile_id
    resolves only for rows bound after profileId persistence landed (older
    bindings need a rebind)."""
    agent_matrix_user_id: str = Field(default="", max_length=255)
    agent_profile_id: str = Field(default="", max_length=128)
    rtc_session_id: str = Field(default="", max_length=128)  # log correlation only


# In-process prewarm ledger: user_id -> {"status", "executor_url", "ts"}.
# Single-host by design today (binding rule #20): the seam to move this out is
# the broker itself — on backend restart, readiness simply re-reports False and
# the next prewarm call re-ensures (idempotent at the broker).
_PREWARM_STATE: dict[str, dict] = {}


async def _executor_alive(executor_url: str) -> bool:
    """200 on /health, never raises (mirrors broker_client._executor_healthy;
    private there, so restated rather than imported)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{executor_url.rstrip('/')}/health")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001 — booting / absent both mean "not ready"
        return False


async def _resolve_prewarm_target(request: Request, body_mxid: str, body_profile: str):
    """Shared auth + resolution for both prewarm endpoints.

    Returns (owner_user_id, None) on success or (None, JSONResponse) on
    failure. Auth is the per-agent bearer_token (constant-time compare) — NOT
    a user JWT; both paths sit in AUTH_EXEMPT_PATHS and self-credential here,
    same pattern as /api/admin/runtime/status.
    """
    if bool(body_mxid) == bool(body_profile):  # neither or both
        return None, JSONResponse(
            status_code=422,
            content={"error": "exactly one of agent_matrix_user_id / agent_profile_id required"},
        )
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return None, JSONResponse(status_code=401, content={"error": "missing bearer"})
    db = await _get_db()
    mgr = NarramessengerCredentialManager(db)
    cred = (
        await mgr.get_by_matrix_user_id(body_mxid)
        if body_mxid
        else await mgr.get_by_profile_id(body_profile)
    )
    if cred is None or not cred.enabled:
        return None, JSONResponse(status_code=404, content={"error": "agent binding not found"})
    if not cred.bearer_token or not hmac.compare_digest(
        token.encode(), cred.bearer_token.encode()
    ):
        return None, JSONResponse(status_code=403, content={"error": "bearer mismatch"})
    owner = await AgentRepository(db).resolve_owner(cred.agent_id)
    if owner is None:
        return None, JSONResponse(status_code=503, content={"error": "owner lookup failed"})
    if not owner:
        return None, JSONResponse(status_code=404, content={"error": "agent has no owner"})
    return owner, None


async def _do_prewarm(user_id: str) -> None:
    """Background warmer — fire-and-forget, so it catches EVERYTHING (an
    unawaited task must never carry a live exception)."""
    try:
        result = await ensure_executor(user_id)
        if result is None:  # broker vanished between guard and call
            _PREWARM_STATE.pop(user_id, None)
            return
        await wait_until_ready(result.url)
        _PREWARM_STATE[user_id] = {
            "status": "ready", "executor_url": result.url, "ts": time.time(),
        }
        logger.info(f"[prewarm] executor ready user={user_id} cold={result.cold_started}")
    except Exception as e:  # noqa: BLE001
        _PREWARM_STATE[user_id] = {"status": "failed", "executor_url": "", "ts": time.time()}
        logger.warning(f"[prewarm] failed user={user_id}: {type(e).__name__}: {e}")


@router.post("/prewarm")
async def prewarm(request: Request, body: PrewarmRequest):
    """NarraMessenger calls this when a call starts ringing (F28): warm the
    owner's executor while the UI shows "connecting". 202 always on success
    paths; prewarm failure must never block the call itself."""
    owner, err = await _resolve_prewarm_target(
        request, body.agent_matrix_user_id, body.agent_profile_id
    )
    if err is not None:
        return err
    state = _PREWARM_STATE.get(owner)
    if state and state["status"] == "ready" and await _executor_alive(state["executor_url"]):
        return JSONResponse(status_code=202, content={"status": "already_warm"})
    if broker_url() is None:  # local/desktop: nothing to warm — never an error
        return JSONResponse(status_code=202, content={"status": "skipped"})
    _PREWARM_STATE[owner] = {"status": "warming", "executor_url": "", "ts": time.time()}
    task = asyncio.create_task(_do_prewarm(owner))
    del task  # exceptions handled inside _do_prewarm
    logger.info(f"[prewarm] requested user={owner} rtc_session={body.rtc_session_id or '-'}")
    return JSONResponse(status_code=202, content={"status": "warming"})


@router.get("/prewarm/status")
async def prewarm_status(
    request: Request, agent_matrix_user_id: str = "", agent_profile_id: str = ""
):
    """Optional readiness probe so the caller can size the "connecting" UI."""
    owner, err = await _resolve_prewarm_target(
        request, agent_matrix_user_id, agent_profile_id
    )
    if err is not None:
        return err
    state = _PREWARM_STATE.get(owner)
    if state and state.get("executor_url"):
        return {"ready": await _executor_alive(state["executor_url"])}
    return {"ready": False}
