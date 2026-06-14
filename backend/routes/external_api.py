"""
@file_name: external_api.py
@author: NarraNexus
@date: 2026-06-11
@description: /v1/external/* route surface for the external API protocol (v0.3).

Routes registered here:
  - GET    /v1/external/healthz                     (no auth — probe)
  - POST   /v1/external/chat/completions            (chat — Step 6)
  - GET    /v1/external/agents/{aid}/sessions       (Step 7)
  - DELETE /v1/external/agents/{aid}/sessions/{sid} (Step 7)

Auth is handled by the middleware in backend/auth.py: every non-healthz
request must carry a valid nxk_ token. By the time a handler runs:
  - request.state.external_api_authed = True
  - request.state.api_key_agent_id    = the agent the token is scoped to
  - request.state.api_key_owner_user_id = the agent owner
  - request.state.api_key_scopes        = list of allowed scopes

This entire router is conditionally registered (see backend/main.py):
when ENABLE_EXTERNAL_API is unset, the routes are not bound and the
path returns FastAPI's default 404.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.agent_runtime.background_run import BackgroundRun
from xyz_agent_context.agent_runtime.cancellation import CancellationToken
from xyz_agent_context.schema import WorkingSource
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


# =============================================================================
# Request / Response models
# =============================================================================


class _ChatMessage(BaseModel):
    role: str
    content: Any  # str or list[content_block]


class _ChatMetadata(BaseModel):
    """Metadata block — the external integrator's identity per-call.

    session_id is the ONLY required field. user_type and context are
    optional.
    """

    session_id: str = Field(..., min_length=1, max_length=256)
    user_type: str = Field(default="guest")
    context: Optional[dict] = None


class ChatCompletionsRequest(BaseModel):
    """Subset of OpenAI chat.completions request body we honor.

    External-API-specific: `metadata.session_id` is REQUIRED. Without it
    the request 400s. Everything else mirrors the Manyfold path
    (model=agent_id, messages=list, stream=bool).
    """

    model: str  # = agent_id; must match the token's agent
    messages: list[_ChatMessage] = Field(default_factory=list)
    stream: bool = False
    metadata: _ChatMetadata


# =============================================================================
# Helpers
# =============================================================================


_USER_TYPE_PERMANENT = "external_user"
_USER_TYPE_GUEST = "external_guest"


def _resolve_user_type(metadata_user_type: str) -> str:
    """Map the integrator's user_type to a NarraNexus users.user_type.

    Anything not explicitly "permanent" / "registered" becomes guest —
    this is the safe default: forgetting to set user_type means the user
    is subject to TTL cleanup (if the owner configured TTL on the
    agent).
    """
    if metadata_user_type in ("permanent", "registered", _USER_TYPE_PERMANENT):
        return _USER_TYPE_PERMANENT
    return _USER_TYPE_GUEST


def _ephemeral_user_id(agent_id: str, session_id: str) -> str:
    """Mint the per-session user_id from agent_id + integrator session_id.

    Format: ``ext_<agent_id last 8>_<sanitised session_id>[:48]``.

    Including the agent_id segment prevents cross-agent collisions when
    two different integrators happen to pick the same session id format.
    Sanitisation collapses anything outside [a-zA-Z0-9_-] to underscore.
    """
    # Take the last 8 chars of agent_id (it's already a prefixed id like
    # "agt_<hex>"); fall back to the whole thing if shorter.
    aid_tail = agent_id[-8:] if len(agent_id) >= 8 else agent_id
    sane = re.sub(r"[^a-zA-Z0-9_-]+", "_", session_id)[:48]
    return f"ext_{aid_tail}_{sane}"


async def _ensure_ephemeral_user(
    db,
    *,
    user_id: str,
    agent_id: str,
    user_type: str,
) -> None:
    """UPSERT a row into `users` for an external session, setting
    `owned_by_agent` so cascade DELETE and TTL GC can find it later.

    Idempotent: re-running for an existing user is a no-op (we
    skip if the row already exists).
    """
    existing = await db.get_one("users", {"user_id": user_id})
    if existing:
        # Sanity check: if an existing row's owned_by_agent doesn't
        # match this agent, something is very wrong (cross-agent
        # collision should be impossible given the agent_id prefix in
        # the user_id). Log loudly but keep going — the request itself
        # is fine.
        if existing.get("owned_by_agent") not in (agent_id, None):
            logger.warning(
                "external_api: existing user_id={!r} has owned_by_agent="
                "{!r} but request is for agent_id={!r}",
                user_id,
                existing.get("owned_by_agent"),
                agent_id,
            )
        return

    await db.insert(
        "users",
        {
            "user_id": user_id,
            "user_type": user_type,
            "role": "user",
            "owned_by_agent": agent_id,
            "display_name": f"external session ({agent_id[-8:]})",
        },
    )
    logger.info(
        "external_api: provisioned ephemeral user_id={!r} "
        "(user_type={!r}, owned_by_agent={!r})",
        user_id,
        user_type,
        agent_id,
    )


def _extract_user_input(messages: list[_ChatMessage]) -> str:
    """Last user-role message becomes the agent input. Multi-modal blocks
    have text-type parts concatenated; image/audio/file blocks ignored.
    """
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if not last_user:
        return ""
    c = last_user.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for blk in c:
            if isinstance(blk, dict) and blk.get("type") == "text":
                txt = blk.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def _error_envelope(message: str, code: str, status: int = 400) -> dict:
    """Standard external-API error shape. Mirrors OpenAI's error envelope
    so SDK clients that look at `error.code` / `error.message` work
    unmodified.
    """
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error" if status < 500 else "api_error",
            "code": code,
        }
    }


def _require_scope(request: Request, scope: str) -> None:
    """Raise 403 if the token doesn't grant the requested scope."""
    scopes = getattr(request.state, "api_key_scopes", []) or []
    if scope not in scopes:
        raise HTTPException(
            status_code=403,
            detail=_error_envelope(
                f"token does not have scope {scope!r} "
                f"(granted: {scopes})",
                code="insufficient_scope",
                status=403,
            ),
        )


# =============================================================================
# Streaming chunk shape (OpenAI-compatible)
# =============================================================================


def _chunk(
    *,
    id_: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> str:
    """Emit one ``data: {…}\\n\\n`` SSE chunk in OpenAI shape."""
    body = {
        "id": id_,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _done_sentinel() -> str:
    return "data: [DONE]\n\n"


# Reuse Manyfold's event classification (imported lazily so cold
# imports don't depend on the manyfold env flag).
def _import_event_classifier():
    """Lazy import of Manyfold's openai_compat helpers — they're already
    battle-tested for the same BackgroundRun event stream and we have
    no need to fork them.
    """
    from backend.routes.openai_compat import (
        _classify_event,
        _is_error,
        _is_terminal,
    )
    return _classify_event, _is_error, _is_terminal


# =============================================================================
# POST /v1/external/chat/completions
# =============================================================================


@router.post("/v1/external/chat/completions")
async def external_chat_completions(
    request: Request, body: ChatCompletionsRequest
):
    """OpenAI-compatible chat completions for external integrators.

    Auth: nxk_ token (middleware-handled). The token is permanently
    scoped to one agent_id; the request's `model` field MUST match.

    Memory: per `metadata.session_id` — each session_id gets its own
    NarraNexus user_id (`ext_<agent>_<session>`) and therefore its own
    narrative/chat/memory pool. Two sessions never see each other.

    Provider: the agent owner's user_providers / user_slots are used
    via the option-c fallback in UserProviderService.get_user_config
    (Step 6 schema work). LLM token spend bills back to the owner.
    """
    _require_scope(request, "chat")

    token_agent_id = getattr(request.state, "api_key_agent_id", None)
    owner_user_id = getattr(request.state, "api_key_owner_user_id", None)
    if not token_agent_id or not owner_user_id:
        # Should never happen if middleware ran; defensive.
        return JSONResponse(
            status_code=401,
            content=_error_envelope(
                "auth state missing on request; middleware did not run",
                code="unauthenticated",
                status=401,
            ),
        )

    agent_id = body.model
    if agent_id != token_agent_id:
        return JSONResponse(
            status_code=403,
            content=_error_envelope(
                f"this token is scoped to agent {token_agent_id!r}, but "
                f"the request's `model` field is {agent_id!r}",
                code="agent_mismatch",
                status=403,
            ),
        )

    session_id = body.metadata.session_id
    user_input = _extract_user_input(body.messages)
    if not user_input:
        return JSONResponse(
            status_code=400,
            content=_error_envelope(
                "messages must contain at least one role:'user' entry "
                "with non-empty text content",
                code="no_user_message",
            ),
        )

    # Mint the ephemeral user_id and ensure the users row exists.
    ephemeral_user_id = _ephemeral_user_id(agent_id, session_id)
    user_type = _resolve_user_type(body.metadata.user_type)
    db = await get_db_client()
    await _ensure_ephemeral_user(
        db,
        user_id=ephemeral_user_id,
        agent_id=agent_id,
        user_type=user_type,
    )

    # Spin up the BackgroundRun against the ephemeral user_id. The
    # provider lookup deep inside the runtime will fall back to the
    # agent owner's config via UserProviderService.get_user_config's
    # owned_by_agent recursion.
    #
    # v0.4: pass `runtime_factory=make_external_runtime_factory()` so the
    # underlying runtime is `ExternalAgentRuntime(policy=EXTERNAL_API_POLICY)`
    # — this is the single seam that turns on per-user memory scoping,
    # visitor-mode identity rendering, AwarenessModule MCP suppression,
    # and the Write/Edit/Bash SDK denylist for this entire run.
    from xyz_agent_context.agent_runtime.external_agent_runtime import (
        make_external_runtime_factory,
    )
    active_runs = request.app.state.active_runs
    cancellation = CancellationToken()
    bg = BackgroundRun(
        agent_id=agent_id,
        user_id=ephemeral_user_id,
        input_preview=user_input,
        db=db,
        active_runs=active_runs,
        cancellation=cancellation,
        runtime_factory=make_external_runtime_factory(),
    )

    broadcaster_session_id = f"ext_{uuid.uuid4().hex[:8]}"
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    bg.task = asyncio.create_task(
        bg.drive(
            agent_id=agent_id,
            user_id=ephemeral_user_id,
            input_content=user_input,
            working_source=WorkingSource.EXTERNAL_API,
            pass_mcp_urls={},
            trigger_extra_data={
                "trigger_id": broadcaster_session_id,
                "retrieval_anchor": user_input,
                "external_session_id": session_id,
                "external_context": body.metadata.context,
            },
        )
    )

    # Wait for BackgroundRun to publish its run_id before subscribing.
    try:
        await asyncio.wait_for(bg.ready_event.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning(
            "external_api[{}]: BackgroundRun never went ready in 30s",
            agent_id,
        )

    subscriber = bg.broadcaster.subscribe(broadcaster_session_id)

    classify_event, is_error, is_terminal = _import_event_classifier()

    # -------- Streaming mode --------
    if body.stream:
        async def gen():
            yield _chunk(
                id_=completion_id,
                created=created_ts,
                model=agent_id,
                delta={"role": "assistant", "content": ""},
            )
            content_emitted = False
            pending_tool_call_ids: list[str] = []
            tool_index = 0
            try:
                async for event in subscriber:
                    if is_error(event):
                        msg = (
                            event.get("message")
                            or event.get("error_message")
                            or event.get("error")
                            or "agent run failed"
                        )
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"content": f"[error] {msg}"},
                        )
                        break

                    classified = classify_event(event)
                    if classified is None:
                        if is_terminal(event):
                            break
                        continue

                    kind, payload = classified
                    if kind == "reasoning":
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"reasoning_content": payload},
                        )
                    elif kind == "content":
                        content_emitted = True
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"content": payload},
                        )
                    elif kind == "tool_call":
                        tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
                        pending_tool_call_ids.append(tool_call_id)
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={
                                "tool_calls": [
                                    {
                                        "index": tool_index,
                                        "id": tool_call_id,
                                        "type": "function",
                                        "function": {
                                            "name": payload.get("name", ""),
                                            "arguments": json.dumps(
                                                payload.get("arguments", {}),
                                                ensure_ascii=False,
                                            ),
                                        },
                                    }
                                ]
                            },
                        )
                        tool_index += 1
                    elif kind == "tool_result":
                        paired_id = (
                            pending_tool_call_ids.pop(0)
                            if pending_tool_call_ids
                            else f"call_{uuid.uuid4().hex[:12]}"
                        )
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={
                                "tool_results": [
                                    {
                                        "tool_call_id": paired_id,
                                        "content": payload,
                                    }
                                ]
                            },
                        )

                    if is_terminal(event):
                        break
            except asyncio.CancelledError:
                logger.info(
                    "external_api[{}]: client disconnected mid-stream",
                    agent_id,
                )
                cancellation.cancel()
                raise

            finish_reason = "stop" if content_emitted else "tool_calls"
            yield _chunk(
                id_=completion_id,
                created=created_ts,
                model=agent_id,
                delta={},
                finish_reason=finish_reason,
            )
            yield _done_sentinel()

        return StreamingResponse(gen(), media_type="text/event-stream")

    # -------- Non-streaming mode --------
    combined_content_parts: list[str] = []
    combined_reasoning_parts: list[str] = []
    accumulated_tool_calls: list[dict] = []
    accumulated_tool_results: list[dict] = []
    pending_tool_call_ids: list[str] = []

    try:
        async for event in subscriber:
            if is_error(event):
                msg = (
                    event.get("message")
                    or event.get("error_message")
                    or event.get("error")
                    or "agent run failed"
                )
                combined_content_parts.append(f"[error] {msg}")
                break
            classified = classify_event(event)
            if classified is None:
                if is_terminal(event):
                    break
                continue
            kind, payload = classified
            if kind == "reasoning":
                combined_reasoning_parts.append(payload)
            elif kind == "content":
                combined_content_parts.append(payload)
            elif kind == "tool_call":
                tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
                pending_tool_call_ids.append(tool_call_id)
                accumulated_tool_calls.append({
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": payload.get("name", ""),
                        "arguments": json.dumps(
                            payload.get("arguments", {}),
                            ensure_ascii=False,
                        ),
                    },
                })
            elif kind == "tool_result":
                paired_id = (
                    pending_tool_call_ids.pop(0)
                    if pending_tool_call_ids
                    else f"call_{uuid.uuid4().hex[:12]}"
                )
                accumulated_tool_results.append({
                    "tool_call_id": paired_id,
                    "content": payload,
                })
            if is_terminal(event):
                break
    except asyncio.CancelledError:
        cancellation.cancel()
        raise

    final_content = "".join(combined_content_parts)
    finish_reason = "stop" if final_content else "tool_calls"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": agent_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": final_content,
                    "reasoning_content": "".join(combined_reasoning_parts),
                    "tool_calls": accumulated_tool_calls or None,
                    "tool_results": accumulated_tool_results or None,
                },
                "finish_reason": finish_reason,
            }
        ],
    }


# =============================================================================
# Healthz + Step 7 placeholders
# =============================================================================


@router.get("/v1/external/healthz")
async def external_healthz():
    """Unauthenticated readiness probe.

    Always returns 200 once the FastAPI app is up. Does NOT touch the
    database — Kubernetes calls this every few seconds and a DB outage
    shouldn't flap pod readiness (we'd return 503 from real handlers
    instead).
    """
    return {"status": "ok", "service": "narranexus-external-api"}


@router.get("/v1/external/agents/{agent_id}/sessions")
async def external_list_sessions(
    agent_id: str,
    request: Request,
    limit: int = 100,
):
    """List ephemeral sessions an integrator has touched on this agent.

    Returned shape mirrors the per-session row the
    EphemeralSessionGCPoller (Step 8) would use for TTL decisions:
    session_id, user_id, message_count, narrative_count, first/last
    activity timestamps. Useful for the integrator to audit "which of my
    sessions are still alive" against their own session-id store.

    Currently limit-capped at 500 to keep response size bounded; if you
    have more than that we recommend the integrator track session_ids
    locally rather than poll this endpoint.
    """
    _require_scope(request, "session.list")
    _validate_agent_match(request, agent_id)

    if limit < 1 or limit > 500:
        return JSONResponse(
            status_code=400,
            content=_error_envelope(
                "limit must be between 1 and 500",
                code="invalid_request",
            ),
        )

    db = await get_db_client()

    # Pull every users row owned_by_agent=this agent (no DB-level
    # ORDER BY supported by our generic .get() filter API; we sort in
    # Python after fetching). Limited to `limit` rows up-front via the
    # backend, then we count messages per-user in a follow-up query.
    user_rows = await db.get(
        "users",
        filters={"owned_by_agent": agent_id},
        limit=limit,
    )

    sessions: list[dict] = []
    for u in user_rows:
        ephemeral_user_id = u["user_id"]
        # Recover the original session_id from the user_id namespace.
        session_id_part = _session_id_from_user_id(ephemeral_user_id, agent_id)

        # Count events for this user (events.user_id is the per-session
        # message ledger). agent_messages doesn't have user_id (it's a
        # channel-class table) so events is the right source.
        msg_count_row = await db.execute(
            "SELECT COUNT(*) AS c FROM events WHERE user_id = ? AND agent_id = ?",
            (ephemeral_user_id, agent_id),
        )
        nar_count_row = await db.execute(
            "SELECT COUNT(*) AS c FROM narratives WHERE agent_id = ?",
            (agent_id,),
        )
        # last_message_at: max(updated_at) from events for this user. Same
        # heuristic the EphemeralSessionGCPoller uses for TTL.
        last_msg_row = await db.execute(
            "SELECT MAX(updated_at) AS m FROM events "
            "WHERE user_id = ? AND agent_id = ?",
            (ephemeral_user_id, agent_id),
        )

        sessions.append({
            "session_id": session_id_part,
            "user_id": ephemeral_user_id,
            "user_type": u.get("user_type"),
            "message_count": (msg_count_row[0]["c"] if msg_count_row else 0),
            # Narratives are per-agent in DB; the count here is the
            # agent-wide total (not per-session). Cheap proxy; if the
            # integrator needs per-session narrative count they can
            # compute it from chat history themselves.
            "agent_narrative_total": (nar_count_row[0]["c"] if nar_count_row else 0),
            "created_at": u.get("create_time"),
            "last_message_at": (
                last_msg_row[0].get("m") if last_msg_row else None
            ),
        })

    # Newest session activity first.
    sessions.sort(
        key=lambda s: s.get("last_message_at") or s.get("created_at") or "",
        reverse=True,
    )

    return {
        "object": "list",
        "agent_id": agent_id,
        "data": sessions,
        "count": len(sessions),
    }


@router.delete("/v1/external/agents/{agent_id}/sessions/{session_id}")
async def external_delete_session(
    agent_id: str, session_id: str, request: Request
):
    """Hard cascade DELETE for the per-session ephemeral user.

    Uses `delete_user_cascade` from Step 2 — wipes the users row and
    every dependent row in 13 child tables, plus the per-(agent, user)
    workspace directories on disk. Idempotent: 404 → 200 success, just
    with zero cascade counts.
    """
    _require_scope(request, "session.delete")
    _validate_agent_match(request, agent_id)

    from xyz_agent_context.utils.user_cascade import delete_user_cascade

    ephemeral_user_id = _ephemeral_user_id(agent_id, session_id)
    db = await get_db_client()

    # Existence check — gives the integrator a signal that they're
    # deleting a real session, not a typo. But idempotent: missing rows
    # still report 200 with all-zero cascade counts.
    cascade = await delete_user_cascade(ephemeral_user_id, db)

    return {
        "deleted": True,
        "session_id": session_id,
        "user_id": ephemeral_user_id,
        "cascade": cascade,
    }


# =============================================================================
# Step 7 helpers
# =============================================================================


def _validate_agent_match(request: Request, requested_agent_id: str) -> None:
    """Reject a session-management request whose agent_id doesn't match
    the token's scoped agent. The middleware already authed the token;
    this just enforces the per-agent scoping at the route layer.
    """
    token_agent_id = getattr(request.state, "api_key_agent_id", None)
    if token_agent_id != requested_agent_id:
        raise HTTPException(
            status_code=403,
            detail=_error_envelope(
                f"token is scoped to agent {token_agent_id!r} but request "
                f"targets agent {requested_agent_id!r}",
                code="agent_mismatch",
                status=403,
            ),
        )


def _session_id_from_user_id(user_id: str, agent_id: str) -> str:
    """Best-effort inverse of `_ephemeral_user_id`: pull the
    sanitised-session part out so the list endpoint can report it.

    NOT a guaranteed roundtrip if the original session_id had characters
    that got collapsed during sanitisation — the integrator should treat
    this as a display label, not as a stable key. (The token they
    persist on their side IS the stable key.)
    """
    aid_tail = agent_id[-8:] if len(agent_id) >= 8 else agent_id
    expected_prefix = f"ext_{aid_tail}_"
    if user_id.startswith(expected_prefix):
        return user_id[len(expected_prefix):]
    return user_id  # unrecognised shape; surface the raw user_id
