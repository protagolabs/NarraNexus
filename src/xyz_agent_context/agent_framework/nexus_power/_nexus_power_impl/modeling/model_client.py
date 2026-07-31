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

from xyz_agent_context.agent_framework.nexus_power.contracts.events import Usage
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelEvent,
    ModelRequest,
    ProviderMessage,
    ProviderProfile,
)


# Told to the gateway, which otherwise defends every client against
# prefill-rejecting upstreams by appending a continuation turn to EVERY
# conversation that ends with an assistant message. That defence costs a
# repeated clause on backends that would have accepted the prefill, and
# it cannot help a client that could simply retry. This loop can (see
# ``NexusPowerLoop`` PREFILL_REJECTED), so it opts out and pays the cost
# only on an actual rejection. The claude CLI cannot, so it stays
# covered. Renaming this header is a deploy-repo lockstep change
# (stacks/narranexus-app/litellm/prefill_compat.py).
PREFILL_SELF_HANDLED_HEADER = {"x-nexus-prefill-retry": "1"}


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
        extra["extra_headers"] = {
            **(extra.get("extra_headers") or {}),
            **PREFILL_SELF_HANDLED_HEADER,
        }
        tools = self._dialect_tools(request.tools, params.base_url, params.provider)

        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        stop_reason = ""

        stream = self._client.stream_chat(
            model=self._litellm_model(params.model, params.base_url, params.provider),
            messages=messages,
            tools=tools or None,
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
            args, parse_error, truncated = _parse_args(raw)
            yield ModelEvent(
                kind="tool_use",
                content_index=index,
                payload={
                    "call_id": call["id"] or f"call_{index}_{uuid.uuid4().hex[:8]}",
                    "tool_name": call["name"],
                    "args": args,
                    "raw_arguments": raw,
                    "parse_error": parse_error,
                    "args_truncated": truncated,
                },
            )
        yield ModelEvent(
            kind="done", payload={"stop_reason": stop_reason, "usage": usage}
        )

    def estimate_cost_usd(self, usage: Usage, model: str) -> float | None:
        return price_usage(usage, model)

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
            elif isinstance(content, list) and content:
                # Already in block form (multimodal, or a prior mark).
                # Skipping these silently just bought less cache than the
                # plan asked for — the breakpoint was computed, then
                # dropped on the floor. Anthropic reads cache_control off
                # the LAST block of the message, so that is where it goes.
                blocks = [dict(b) if isinstance(b, dict) else b for b in content]
                last = blocks[-1]
                if isinstance(last, dict):
                    last["cache_control"] = {"type": "ephemeral"}
                    message["content"] = blocks
        return marked

    @staticmethod
    def _dialect_tools(
        tools: list[dict[str, Any]], base_url: str, provider: str | None = None
    ) -> list[dict[str, Any]]:
        """Anthropic-native tool defs for custom anthropic-protocol
        endpoints.

        litellm's OpenAI→Anthropic conversion stamps ``type: "custom"``
        on tool defs (current Anthropic spec); strict third-party
        gateways (e.g. NetMind's DeepSeek backend, Rust serde) reject
        the unknown variant. litellm passes ANTHROPIC-shaped tools
        (``input_schema`` present) through untouched, so for custom
        endpoints we hand it the native shape ourselves — no ``type``
        field at all. Official-API calls keep the OpenAI shape (litellm
        handles those correctly end-to-end).
        """
        # OpenAI-protocol endpoints take the OpenAI tool shape as-is;
        # only the anthropic route needs the native rewrite below.
        if not base_url or not tools or (provider or "").lower() == "openai":
            return tools
        native: list[dict[str, Any]] = []
        for tool in tools:
            function = tool.get("function") or {}
            if not function:
                native.append(tool)
                continue
            native.append(
                {
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return native

    @staticmethod
    def _litellm_model(model: str, base_url: str, provider: str | None) -> str:
        """Force the route that matches the bound provider's PROTOCOL.

        A custom ``base_url`` means litellm cannot infer the dialect from
        the model id — and it must not try: platform model ids contain
        slashes (``minimax/minimax-m2.5``, ``deepseek-ai/DeepSeek-V3``)
        which litellm would read as a provider prefix and route wrongly
        (measured: an openai-protocol card answered with
        ``AnthropicException``). So we state the route explicitly, from
        the protocol the resolver already decided. Without a custom
        ``base_url`` the name passes through untouched (callers may use
        litellm's native routing syntax).

        The route is prepended UNCONDITIONALLY: platform ids can embed
        the route name itself (NetMind's ``anthropic/claude-sonnet-5``,
        ``openai/gpt-5.4``), and litellm always eats the first segment
        as its routing prefix. Skipping the prepend for "already routed"
        ids sent the bare tail upstream, which NetMind rejects with
        404 "unknown model" — it has no bare aliases.
        """
        route = "openai" if (provider or "").lower() == "openai" else "anthropic"
        if not base_url:
            return model
        return f"{route}/{model}"


class AnthropicDirectClient:
    """Bypass seat: Anthropic's native SDK instead of litellm.

    Written only if the four passthrough probes show litellm mangling a
    dialect (cache_control, signed thinking replay, argument deltas).
    Same protocol, so switching is an assembly swap and nothing above
    notices. Not assembled today — instantiating it is a wiring error,
    which is why it fails loudly rather than silently degrading.
    """

    profile: ProviderProfile

    def __init__(self, profile: ProviderProfile) -> None:
        self.profile = profile

    def estimate_cost_usd(self, usage: Usage, model: str) -> float | None:
        """Pricing is provider-independent — same map as everyone else."""
        return price_usage(usage, model)

    async def stream_step(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        raise NotImplementedError(
            "AnthropicDirectClient is the bypass seat; assemble "
            "LiteLLMModelClient unless a measured dialect failure says otherwise"
        )
        yield  # pragma: no cover - makes this an async generator


def _parse_args(raw: str) -> tuple[dict[str, Any], str | None, bool]:
    """(args, parse_error, truncated). Broken argument JSON is NOT
    smuggled through under a synthetic key — the old ``{"_raw": raw}``
    fallback let a truncated call execute with its real fields missing,
    and the tool's complaint pointed everywhere but the truncation. The
    error travels explicitly so the dispatch layer can answer the call
    instead."""
    if not raw:
        return {}, None, False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"{exc.msg} at char {exc.pos} of {len(raw)}", _is_cut_short(raw, exc)
    if not isinstance(parsed, dict):
        return {}, f"expected a JSON object, got {type(parsed).__name__}", False
    return parsed, None, False


def _is_cut_short(raw: str, exc: json.JSONDecodeError) -> bool:
    """Whether ``raw`` is a valid JSON prefix that simply ran out.

    Read off the bytes rather than the provider's ``stop_reason``: the
    NetMind free-tier gateway reports ``tool_use`` for a call its own
    output cap severed (reproduced 2026-07-31 — max_tokens=2000,
    output_tokens=2000, arguments cut to ``{"path": "game.html"``), and
    trusting it sent the model chasing an escaping bug that did not
    exist.

    Two signatures, both exhaustive over CPython's decoder: a string that
    never closes (the cut landed inside a value), or a failure at the
    very end of the buffer (it landed between tokens). Genuine damage —
    a bad escape, a stray delimiter, a raw control character — always
    fails STRICTLY INSIDE the buffer, which is what keeps the two apart.
    """
    return exc.msg.startswith("Unterminated string") or exc.pos >= len(raw)


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


def price_usage(usage: Usage, model: str) -> float | None:
    """Price a turn from litellm's maintained cost map.

    litellm ships (and keeps current) per-model prices for hundreds of
    models — far better than a table we would hand-maintain and let rot.
    Cached reads use their discounted rate when the map provides one.
    Unknown model → None, which the platform records as "no price
    available" rather than a confidently wrong number.
    """
    prices = _price_row(model)
    if not prices:
        return None
    input_rate = prices.get("input_cost_per_token") or 0.0
    output_rate = prices.get("output_cost_per_token") or 0.0
    cache_read_rate = prices.get("cache_read_input_token_cost")
    if cache_read_rate is None:
        cache_read_rate = input_rate
    cache_write_rate = prices.get("cache_creation_input_token_cost") or input_rate
    return (
        usage.input_tokens * input_rate
        + usage.output_tokens * output_rate
        + usage.cache_read_tokens * cache_read_rate
        + usage.cache_creation_tokens * cache_write_rate
    )


def _price_row(model: str) -> dict[str, Any] | None:
    """litellm's price row for a model id, tolerant of route prefixes.

    ``anthropic/deepseek-ai/DeepSeek-V4-Pro`` and the bare id both
    resolve; the map is keyed by several forms, so try most-specific
    first, then a case-insensitive sweep.
    """
    # Through the seam, never `import litellm` here: that class declares
    # itself the repo's single litellm import point, and a second one
    # makes the claim false and the swap it protects (different client,
    # direct SDK) a two-file change instead of one — iron rule #9
    # (2026-07-29 review).
    from xyz_agent_context.agent_framework.llm.litellm_client import LitellmClient

    try:
        table = LitellmClient.model_cost_map()
    except ImportError:  # pragma: no cover - litellm is a hard dependency
        return None
    candidates = [model]
    if "/" in model:
        candidates.append(model.split("/", 1)[1])
        candidates.append(model.rsplit("/", 1)[-1])
    for candidate in candidates:
        row = table.get(candidate)
        if row:
            return row
    lowered = model.lower()
    for key, row in table.items():
        if key.lower() == lowered:
            return row
    return None
