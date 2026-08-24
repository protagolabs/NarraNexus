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
import itertools

from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from loguru import logger

from xyz_agent_context.agent_framework.loop.broker_client import (
    ExecutorEnsureResult,
    broker_url,
    ensure_executor,
    executor_healthy,
    wait_until_ready,
)
from xyz_agent_context.agent_runtime.executor_reaper import no_live_recorded_run_for
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
    # max_length 64 == the nexus_profile_id column (VARCHAR(64)): anything
    # longer can never resolve, so tightening only turns the 404 into an
    # earlier 422.
    agent_profile_id: str = Field(default="", max_length=64)
    rtc_session_id: str = Field(default="", max_length=128)  # log correlation only


# In-process prewarm ledger: user_id -> {"status", "executor_url", "gen", "task"}.
# Single-host by design today (binding rule #20): the seam to move this out is
# the broker itself — on backend restart, readiness simply re-reports False and
# the next prewarm call re-ensures (idempotent at the broker).
#
# The entry doubles as the STRONG reference to the in-flight warmer task (the
# event loop only keeps weak refs — asyncio docs): an entry is only ever
# replaced by a newer generation, which holds its own task ref; the superseded
# task keeps running harmlessly and its generation-guarded writes no-op.
_PREWARM_STATE: dict[str, dict] = {}

# Monotonic generation per warmer task: every ledger write inside _do_prewarm
# is guarded by "my gen is still the current entry's gen", so a stale task can
# never clobber a newer entry (e.g. a stale failure-pop landing after a
# newer request already started warming).
_PREWARM_GEN = itertools.count(1)


async def _resolve_prewarm_target(
    request: Request, body_mxid: str, body_profile: str
) -> tuple[Optional[str], Optional[JSONResponse]]:
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


def _owns_ledger_entry(user_id: str, gen: int) -> bool:
    """True iff the current ledger entry is this warmer's own generation."""
    return _PREWARM_STATE.get(user_id, {}).get("gen") == gen


async def _do_prewarm(user_id: str, gen: int) -> None:
    """Background warmer — fire-and-forget, so it catches EVERYTHING (an
    unawaited task must never carry a live exception).

    Every ledger write is generation-guarded: if a newer prewarm replaced
    our entry while we awaited, our late result must not clobber it. The
    ready write mutates the entry in place so the entry keeps holding this
    task's strong reference; failure (like broker-vanished) pops the entry
    entirely — the next POST re-warms either way.
    """
    try:
        # Prewarm is the RIGHT place to roll a stale executor image: it is
        # not a run, and its whole purpose is to move cold-start out of the
        # user's turn. Leaving the default False here would defer the
        # replacement to this user's next turn — so the first interaction
        # after every image rebuild would warm the OLD image, then pay a full
        # stop + await-gone + run + wait_until_ready inline, with the prewarm
        # wasted.
        #
        # No run to exclude: this is not one.
        #
        # Residual risk, stated rather than argued away: the verdict covers
        # RECORDED RUNS only. A live office-watch session runs inside this
        # same container and is invisible to it, so a replacement authorised
        # here can take that session down. "Prewarm itself holds nothing on
        # the container" would be the wrong argument — the authorisation is
        # about the container, not about this caller.
        #
        # Accepted for now because the idle reaper already destroys such a
        # container on its 20-minute TTL (office-watch does not refresh the
        # admission ledger either), so this adds a trigger moment rather than
        # a new class of failure. The fix is one liveness rule that sees
        # non-run holders too: have watch/ensure record a lease row that
        # live_run_elsewhere reads, so all three consumers inherit it. That
        # belongs in its own change, not here.
        result = await ensure_executor(
            user_id, allow_stale_replace=await no_live_recorded_run_for(user_id)
        )
        if result is None:  # broker vanished between guard and call
            if _owns_ledger_entry(user_id, gen):
                _PREWARM_STATE.pop(user_id, None)
            return
        await wait_until_ready(result.url)
        if _owns_ledger_entry(user_id, gen):
            state = _PREWARM_STATE[user_id]
            state["status"] = "ready"
            state["executor_url"] = result.url
        logger.info(f"[prewarm] executor ready user={user_id} cold={result.cold_started}")
    except Exception as e:  # noqa: BLE001
        # A failed entry carries no information the next POST could use —
        # it re-warms regardless — so drop the entry instead of parking a
        # "failed" status in the ledger.
        if _owns_ledger_entry(user_id, gen):
            _PREWARM_STATE.pop(user_id, None)
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
    # timeout=1.0: the caller is mid-ring — a wedged container must not cost
    # them the full 5s default before we fall through to re-warming.
    if (
        state
        and state["status"] == "ready"
        and await executor_healthy(state["executor_url"], timeout=1.0)
    ):
        return JSONResponse(status_code=202, content={"status": "already_warm"})
    if (
        state
        and state["status"] == "warming"
        and state["task"] is not None
        and not state["task"].done()
    ):
        # In-flight dedup: a call rings once but the partner may POST several
        # times — piling extra ensure_executor calls onto the broker helps
        # nobody. The live task will flip this entry to ready, or pop it
        # on failure.
        return JSONResponse(status_code=202, content={"status": "warming"})
    if broker_url() is None:  # local/desktop: nothing to warm — never an error
        return JSONResponse(status_code=202, content={"status": "skipped"})
    gen = next(_PREWARM_GEN)
    entry: dict[str, Any] = {
        "status": "warming", "executor_url": "", "gen": gen, "task": None,
    }
    _PREWARM_STATE[owner] = entry
    # Store the entry BEFORE creating the task, then patch the task ref in.
    # No await separates create_task from the patch, so neither the new task
    # nor another request can ever observe entry["task"] is None — while the
    # reverse order (task first, entry after) would let a fast task's ledger
    # write be clobbered by our own late entry store.
    entry["task"] = asyncio.create_task(_do_prewarm(owner, gen))
    logger.info(f"[prewarm] requested user={owner} rtc_session={body.rtc_session_id or '-'}")
    return JSONResponse(status_code=202, content={"status": "warming"})


@router.get("/prewarm/status")
async def prewarm_status(
    request: Request,
    agent_matrix_user_id: str = Query(default="", max_length=255),
    agent_profile_id: str = Query(default="", max_length=64),
):
    """Optional readiness probe so the caller can size the "connecting" UI.

    Query constraints mirror the POST body's (PrewarmRequest): same fields,
    same bounds.
    """
    owner, err = await _resolve_prewarm_target(
        request, agent_matrix_user_id, agent_profile_id
    )
    if err is not None:
        return err
    state = _PREWARM_STATE.get(owner)
    if state and state.get("executor_url"):
        # timeout=1.0 like the POST's already-warm probe: the partner polls
        # this mid-ring — a wedged container must not stall the poll for the
        # 5s default when "not ready yet" is a perfectly good answer.
        return {"ready": await executor_healthy(state["executor_url"], timeout=1.0)}
    return {"ready": False}
