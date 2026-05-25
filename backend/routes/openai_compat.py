"""
@file_name: openai_compat.py
@author: NexusAgent
@date: 2026-05-25
@description: OpenAI-compatible chat completions endpoint for Manyfold integration

Exposes ``POST /v1/chat/completions`` so Manyfold platform's
``ApiChatAdapter`` (modelled on openclaw.adapter.ts:90-328) can drive
NarraNexus agents using the standard OpenAI protocol.

Wiring rules (Owner decisions, 2026-05-25):
  * ``model`` field on the request = agent_id (NOT a model name).
  * Streaming: emit OpenAI-shape SSE chunks; finish_reason="stop" + final
    ``data: [DONE]`` sentinel.
  * Each chunk echoes ``model`` = agent_id (the platform-provided value),
    including error responses.
  * Bearer ``MANYFOLD_GATEWAY_TOKEN`` is required (auth middleware filters
    before this handler runs).
  * Endpoint is registered only when ``ENABLE_MANYFOLD_API=1`` — see
    backend/main.py conditional include.

Event-to-chunk translation (minimal viable — see spec Part 4.4):
  * agent_response (text_delta) → choices[0].delta.content
  * progress events with ``send_message_to_user_directly`` tool name →
    treated as the user-visible reply, content piped into delta.content
  * terminal (run_ended / done / completed / failed / cancelled) →
    final chunk with finish_reason="stop" + [DONE]
  * error → OpenAI error envelope shape

Deferred (tracked in spec Appendix B): a dedicated ``reply_to_manyfold``
MCP tool + manyfold_module would let the agent be explicit about which
events are user-visible. For v1 we lean on the existing
``send_message_to_user_directly`` tool the chat module already provides.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
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


# ---------------------------------------------------------------------------
# Request / Response models (OpenAI shape — only fields we read or echo)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: Any  # str or list[content_block]


class ChatCompletionsRequest(BaseModel):
    """Subset of the OpenAI chat.completions request body we honor.

    Platform code only relies on ``model``, ``messages``, ``stream`` per
    openclaw.adapter.ts:118+. Unknown fields are ignored (Pydantic default).
    """

    model: str  # = agent_id per Manyfold contract
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False


# ---------------------------------------------------------------------------
# Auth helper (middleware sets request.state.manyfold_authed already; this
# helper raises a uniform 401 if for some reason it didn't fire)
# ---------------------------------------------------------------------------


def _require_manyfold_auth(request: Request, model_echo: str = "") -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail=_openai_error(
                "missing or invalid MANYFOLD_GATEWAY_TOKEN",
                etype="invalid_request_error",
                model_echo=model_echo,
            ),
        )


# ---------------------------------------------------------------------------
# OpenAI envelope helpers
# ---------------------------------------------------------------------------


def _openai_error(
    message: str,
    *,
    etype: str = "invalid_request_error",
    code: Optional[str] = None,
    model_echo: str = "",
) -> dict:
    """Build an OpenAI-shape error body. The ``model`` echo is required
    per Owner decision (2026-05-25, Part 4.4): error responses must echo
    the requested agent_id so platform-side logs aggregate correctly."""
    err: dict[str, Any] = {"message": message, "type": etype}
    if code:
        err["code"] = code
    return {"error": err, "model": model_echo}


def _chunk(
    *,
    id_: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> str:
    """Format one OpenAI SSE chunk as a ``data: {...}\\n\\n`` line."""
    payload = {
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
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _done_sentinel() -> str:
    return "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Agent + creator resolution
# ---------------------------------------------------------------------------


async def _resolve_agent_creator(agent_id: str) -> Optional[str]:
    """Look up agents.created_by for the agent. Returns None if not found.
    Same pattern as channel_trigger_base._resolve_agent_owner; we don't
    import that here to avoid pulling the entire ChannelTriggerBase ABC."""
    db = await get_db_client()
    row = await db.get_one("agents", {"agent_id": agent_id})
    if not row:
        return None
    return row.get("created_by")


def _extract_user_input(messages: list[ChatMessage]) -> str:
    """Last ``user`` role message's content becomes the agent input. If the
    content is a list (OpenAI multimodal), join text blocks; ignore others.
    Empty string is allowed (some platforms send empty pings)."""
    last_user = next(
        (m for m in reversed(messages) if m.role == "user"),
        None,
    )
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


# ---------------------------------------------------------------------------
# Event → OpenAI delta translation
# ---------------------------------------------------------------------------

_REPLY_TOOL_NAMES = (
    # Chat module's canonical user-visible reply path.
    "send_message_to_user_directly",
    # MCP-prefixed variants emitted by fastmcp.
    "chat_module__send_message_to_user_directly",
    "mcp__chat_module__send_message_to_user_directly",
)

_TERMINAL_TYPES = ("run_ended", "completed", "done", "failed", "cancelled")


def _extract_reply_content(event: dict) -> Optional[str]:
    """Pull user-visible reply text out of a BackgroundRun broadcaster event.

    We accept three shapes (in priority order):
      1. ``agent_response`` with ``delta`` — streaming text from chat module
      2. ``progress`` carrying a ``send_message_to_user_directly`` tool call
         (its ``arguments.content`` is the reply payload)
      3. ``agent_tool_call`` for the same tool
    Anything else returns None and is skipped.
    """
    t = event.get("type", "")
    if t == "agent_response":
        d = event.get("delta")
        if isinstance(d, str) and d:
            return d
        return None
    if t in ("progress", "agent_tool_call"):
        details = event.get("details") or {}
        tool_name = details.get("tool_name", "") or ""
        if not any(name in tool_name for name in _REPLY_TOOL_NAMES):
            return None
        args = details.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                return None
        if not isinstance(args, dict):
            return None
        content = args.get("content")
        return content if isinstance(content, str) and content else None
    return None


def _is_terminal(event: dict) -> bool:
    t = event.get("type", "")
    return t in _TERMINAL_TYPES


def _is_error(event: dict) -> bool:
    return event.get("type") == "error"


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionsRequest):
    agent_id = body.model
    _require_manyfold_auth(request, model_echo=agent_id)

    if not agent_id:
        return JSONResponse(
            status_code=400,
            content=_openai_error(
                "missing 'model' field (must be agent_id)",
                model_echo="",
            ),
        )

    creator = await _resolve_agent_creator(agent_id)
    if not creator:
        return JSONResponse(
            status_code=404,
            content=_openai_error(
                f"agent {agent_id!r} not found",
                etype="invalid_request_error",
                code="agent_not_found",
                model_echo=agent_id,
            ),
        )

    user_input = _extract_user_input(body.messages)

    db = await get_db_client()
    active_runs = request.app.state.active_runs
    cancellation = CancellationToken()
    bg = BackgroundRun(
        agent_id=agent_id,
        user_id=creator,
        input_preview=user_input or "",
        db=db,
        active_runs=active_runs,
        cancellation=cancellation,
    )

    session_id = f"manyfold_{uuid.uuid4().hex[:8]}"

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    # Kick off the background agent run.
    bg.task = asyncio.create_task(
        bg.drive(
            agent_id=agent_id,
            user_id=creator,
            input_content=user_input,
            working_source=WorkingSource.MANYFOLD,
            pass_mcp_urls={},
            trigger_extra_data={"trigger_id": session_id},
        )
    )

    # Wait until BackgroundRun publishes its run_id (Step 0 emitted),
    # otherwise the broadcaster subscribe race might miss early events.
    try:
        await asyncio.wait_for(bg.ready_event.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning(f"[manyfold:{agent_id}] BackgroundRun never went ready in 30s")

    subscriber = bg.broadcaster.subscribe(session_id)

    # -------- Streaming mode --------
    if body.stream:
        async def gen():
            # Initial chunk with role (OpenAI spec — first chunk carries
            # the assistant role, subsequent chunks carry only content
            # deltas).
            yield _chunk(
                id_=completion_id,
                created=created_ts,
                model=agent_id,
                delta={"role": "assistant", "content": ""},
            )
            try:
                async for event in subscriber:
                    if _is_error(event):
                        # Best-effort: surface the error message as a content
                        # token, then terminate. The OpenAI streaming protocol
                        # does not have a great error-mid-stream story; this
                        # is consistent with how openclaw handles it.
                        msg = event.get("message") or event.get("error") or "agent error"
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"content": f"\n[error] {msg}"},
                        )
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={},
                            finish_reason="stop",
                        )
                        yield _done_sentinel()
                        return
                    if _is_terminal(event):
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={},
                            finish_reason="stop",
                        )
                        yield _done_sentinel()
                        return
                    content = _extract_reply_content(event)
                    if content:
                        yield _chunk(
                            id_=completion_id,
                            created=created_ts,
                            model=agent_id,
                            delta={"content": content},
                        )
                # Subscriber iterator exhausted (broadcaster closed cleanly)
                # without an explicit terminal event — still send sentinel.
                yield _chunk(
                    id_=completion_id,
                    created=created_ts,
                    model=agent_id,
                    delta={},
                    finish_reason="stop",
                )
                yield _done_sentinel()
            finally:
                bg.broadcaster.unsubscribe(session_id)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -------- Non-streaming mode (aggregate then return one response) --------
    parts: list[str] = []
    error_msg: Optional[str] = None
    try:
        async for event in subscriber:
            if _is_error(event):
                error_msg = event.get("message") or event.get("error") or "agent error"
                break
            if _is_terminal(event):
                break
            content = _extract_reply_content(event)
            if content:
                parts.append(content)
    finally:
        bg.broadcaster.unsubscribe(session_id)

    if error_msg:
        return JSONResponse(
            status_code=500,
            content=_openai_error(
                error_msg,
                etype="api_error",
                model_echo=agent_id,
            ),
        )

    full_text = "".join(parts)
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
                    "content": full_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
