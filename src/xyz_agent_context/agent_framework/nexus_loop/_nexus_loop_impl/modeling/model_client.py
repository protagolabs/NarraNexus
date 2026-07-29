"""
@file_name: model_client.py
@author: Bin Liang
@date: 2026-07-29
@description: The default ModelClient — translates the litellm chunk
stream into typed ModelEvents and applies the provider dialect.

Division of labour: ``LitellmClient`` owns connections and passthrough;
this class owns SEMANTICS — cache-plan translation, usage-vocabulary
normalization, and the event cut (``tool_use_start`` fires the moment a
name arrives so policy can veto before arguments even start streaming;
``arg_delta`` carries raw fragments; ``tool_use`` closes with complete
parsed arguments).

A bypass client (direct provider SDK) is added only when a dialect's
passthrough measurably fails — same protocol, assembly swap.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import Usage
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    ModelEvent,
    ModelRequest,
    ProviderMessage,
    ProviderProfile,
)


class LiteLLMModelClient:
    """Every provider goes through litellm first (dialects are data)."""

    def __init__(self, profile: ProviderProfile, client: Any) -> None:
        self.profile = profile
        self._client = client  # LitellmClient-shaped (injectable for tests)

    async def stream_step(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        params = request.params
        messages = self._apply_cache_plan(request)
        extra = dict(params.extra)
        extra.setdefault("max_tokens", self.profile.max_output_tokens)

        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        stop_reason = ""

        stream = self._client.stream_chat(
            model=self._litellm_model(params.model, params.base_url),
            messages=messages,
            tools=request.tools or None,
            api_key=params.api_key or None,
            base_url=params.base_url or None,
            extra=extra,
        )
        async for chunk in stream:
            chunk_usage = _extract_usage(chunk.get("usage"))
            if chunk_usage is not None:
                usage = chunk_usage
            for choice in chunk.get("choices") or ():
                if choice.get("finish_reason"):
                    stop_reason = str(choice["finish_reason"])
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if isinstance(text, str) and text:
                    yield ModelEvent(kind="text_delta", payload={"text": text})
                thinking = delta.get("reasoning_content")
                if isinstance(thinking, str) and thinking:
                    yield ModelEvent(kind="thinking_delta", payload={"text": thinking})
                for fragment in delta.get("tool_calls") or ():
                    index = int(fragment.get("index") or 0)
                    call = calls.setdefault(
                        index, {"id": "", "name": "", "arguments": []}
                    )
                    if fragment.get("id"):
                        call["id"] = str(fragment["id"])
                    function = fragment.get("function") or {}
                    if function.get("name"):
                        call["name"] += str(function["name"])
                        yield ModelEvent(
                            kind="tool_use_start",
                            content_index=index,
                            payload={
                                "call_index": index,
                                "call_id": call["id"],
                                "tool_name": call["name"],
                            },
                        )
                    arguments = function.get("arguments")
                    if isinstance(arguments, str) and arguments:
                        call["arguments"].append(arguments)
                        yield ModelEvent(
                            kind="arg_delta",
                            content_index=index,
                            payload={"call_index": index, "text": arguments},
                        )

        for index in sorted(calls):
            call = calls[index]
            raw = "".join(call["arguments"])
            yield ModelEvent(
                kind="tool_use",
                content_index=index,
                payload={
                    "call_id": call["id"] or f"call_{index}_{uuid.uuid4().hex[:8]}",
                    "tool_name": call["name"],
                    "args": _parse_args(raw),
                    "raw_arguments": raw,
                },
            )
        yield ModelEvent(
            kind="done", payload={"stop_reason": stop_reason, "usage": usage}
        )

    # -- dialect application ------------------------------------------

    def _apply_cache_plan(self, request: ModelRequest) -> list[ProviderMessage]:
        """Inject Anthropic-style cache_control at planned breakpoints."""
        if (
            self.profile.cache_style != "breakpoints"
            or not request.cache_plan.breakpoint_indices
        ):
            return request.messages
        marked = [dict(m) for m in request.messages]
        for index in request.cache_plan.breakpoint_indices:
            if not 0 <= index < len(marked):
                continue
            message = marked[index]
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
        return marked

    @staticmethod
    def _litellm_model(model: str, base_url: str) -> str:
        """Route custom Anthropic-protocol endpoints explicitly.

        With a custom ``base_url`` the endpoint IS Anthropic-protocol
        (that is how every platform provider runs through the claude CLI
        today), so the ``anthropic/`` route is FORCED — model ids may
        themselves contain slashes (``minimax/minimax-m2.5``) and must
        never be mistaken for litellm provider prefixes. Without a
        ``base_url`` the name passes through (callers may use native
        litellm routing syntax).
        """
        if base_url:
            return model if model.startswith("anthropic/") else f"anthropic/{model}"
        return model


def _parse_args(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


def _extract_usage(raw: Any) -> Usage | None:
    """Normalize litellm usage (either vocabulary) into ``Usage``."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        dump = getattr(raw, "model_dump", None)
        raw = dump() if callable(dump) else vars(raw)
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    details = raw.get("prompt_tokens_details")
    if not cache_read and isinstance(details, dict):
        cache_read = int(details.get("cached_tokens") or 0)
    cache_creation = int(raw.get("cache_creation_input_tokens") or 0)
    return Usage(
        # Anthropic reports input EXCLUSIVE of cache reads; OpenAI-style
        # cached_tokens are INCLUSIVE. Normalize to exclusive.
        input_tokens=max(0, prompt - cache_read) if _inclusive(raw) else prompt,
        output_tokens=completion,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )


def _inclusive(raw: dict[str, Any]) -> bool:
    return "cache_read_input_tokens" not in raw and isinstance(
        raw.get("prompt_tokens_details"), dict
    )
