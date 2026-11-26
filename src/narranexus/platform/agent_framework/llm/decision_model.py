"""
@file_name: decision_model.py
@author: NetMind.AI
@date: 2026-11-25
@description: Vendor-neutral decision-model client — one request, one
calibrated yes/no probability per question — plus the per-turn credential
resolution that feeds it.

A decision model answers typed questions about a state with a probability and
nothing else, so it gets its own API instead of being bent through
`helper_sdk.llm_function`. The public types name no vendor (binding rule #9);
the one implementation speaks the `/alpha/decisions` wire contract:

    POST {base_url}/alpha/decisions
    {"model": ..., "state": ..., "questions": {"<key>": {"type": "noul", "instructions": ...}}}
    -> {"answers": {"<key>": {"type": "noul", "noul": 0.93}}, "model": ...,
        "usage": {"input_tokens": N, "output_tokens": M}}

Swapping vendors means re-implementing `ask_yes_no`; callers do not change.

Failure is never a score: anything but a finite [0, 1] number for every asked
key raises `DecisionModelError` — a guessed 0.0 is indistinguishable
downstream from a real answer. No retry: callers are on a latency budget.

The key goes only into the Authorization header. A key that is not printable
ASCII without whitespace is never a credential (it could not be sent, and the
transport's error would echo it). Error text is built here: transport failures
carry only the exception type, server text is scrubbed of the key, and raw
exceptions are not chained. `DecisionModelError.category` is the only part
meant for user-visible surfaces.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence
from urllib.parse import urlparse

import httpx

#: The only question type the client asks today: "the probability of yes".
_QUESTION_TYPE = "noul"
_DECISIONS_PATH = "/alpha/decisions"
#: OAuth tokens are bound to their issuer and never valid elsewhere.
_API_KEY_AUTH_TYPES = frozenset({"api_key", "bearer_token"})
#: How much of an error body is quoted into the (log-only) detail.
_ERROR_BODY_EXCERPT_CHARS = 200

#: Where a resolved key came from. A borrowed key's usage is the owning
#: card's spend; the platform key's and an agent-scoped key's are not.
KEY_SOURCE_AGENT_SCOPED = "agent_scoped"
KEY_SOURCE_PLATFORM = "platform"
KEY_SOURCE_HELPER_SLOT = "helper_llm"


class DecisionModelError(Exception):
    """The decision model produced no usable answer. Carries no score.

    ``category`` is a fixed vocabulary (``unconfigured``, ``invalid_request``,
    ``timeout``, ``transport``, ``http_<status>``, ``malformed``) safe to show
    anywhere; ``str(e)`` is log-only detail.
    """

    def __init__(self, category: str, detail: str):
        super().__init__(detail)
        self.category = category


@dataclass(frozen=True)
class YesNoQuestion:
    """One yes/no question. ``instructions`` must be JSON-serialisable
    (str, dict or list); ``key`` names the answer in `DecisionResult.scores`."""

    key: str
    instructions: Any


@dataclass(frozen=True)
class DecisionResult:
    """P(yes) per question key, plus what the call cost."""

    scores: Dict[str, float]
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_ms: int


@dataclass(frozen=True)
class DecisionEndpoint:
    """Where and how to ask, and whose key it is."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float
    key_source: str = KEY_SOURCE_PLATFORM
    #: Told when the endpoint answers 401, so whoever issued the key can stop
    #: handing it out. Not called on any other failure.
    on_rejected: Optional[Callable[[], None]] = field(
        default=None, repr=False, compare=False
    )

    @property
    def configured(self) -> bool:
        """True when a request could be sent at all."""
        return bool(self.base_url.strip() and self.model.strip()) and is_sendable_key(
            self.api_key
        )


@dataclass(frozen=True)
class AgentScopedKey:
    """A key issued for ONE agent by the endpoint it must be sent to (e.g. a
    capped gateway key). ``on_rejected`` lets the issuer drop a key the
    endpoint no longer accepts."""

    base_url: str
    api_key: str = field(repr=False)
    on_rejected: Optional[Callable[[], None]] = field(
        default=None, repr=False, compare=False
    )


def is_sendable_key(key: str) -> bool:
    """Non-empty printable ASCII with no whitespace — the only keys that can go
    into a header without the transport rejecting (and echoing) them."""
    return bool(key) and all("!" <= ch <= "~" for ch in key)


def serialise(value: Any) -> str:
    """The one JSON encoding the request body uses — public so a caller that
    records "how much did we send" counts the same characters."""
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def encode_questions(questions: Sequence[YesNoQuestion]) -> Dict[str, Any]:
    """The ``questions`` object of the request body, keyed by question key."""
    return {
        q.key: {"type": _QUESTION_TYPE, "instructions": q.instructions}
        for q in questions
    }


async def ask_yes_no(
    endpoint: DecisionEndpoint,
    state: Any,
    questions: Sequence[YesNoQuestion],
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> DecisionResult:
    """Ask every question about ``state`` in ONE request.

    Raises `DecisionModelError` on any failure. ``client`` is injectable for
    tests; production gets a short-lived client per call (the house pattern).
    """
    if not endpoint.configured:
        raise DecisionModelError("unconfigured", "decision model endpoint is not configured")
    keys = [q.key for q in questions]
    if not keys or any(not k for k in keys) or len(set(keys)) != len(keys):
        raise DecisionModelError(
            "invalid_request", "question keys must be present, non-empty and unique"
        )

    try:
        body = serialise(
            {"model": endpoint.model, "state": state, "questions": encode_questions(questions)}
        )
    except (TypeError, ValueError) as e:
        raise DecisionModelError(
            "invalid_request", f"request is not JSON-serialisable: {type(e).__name__}"
        ) from None

    url = endpoint.base_url.rstrip("/") + _DECISIONS_PATH
    headers = {
        "Authorization": f"Bearer {endpoint.api_key}",
        "Content-Type": "application/json",
    }
    started = time.monotonic()
    try:
        async with asyncio.timeout(endpoint.timeout_seconds):
            async with (
                contextlib.nullcontext(client)
                if client is not None
                else httpx.AsyncClient(timeout=endpoint.timeout_seconds)
            ) as http:
                response = await http.post(
                    url, content=body.encode("utf-8"), headers=headers,
                    timeout=endpoint.timeout_seconds,
                )
    except (TimeoutError, httpx.TimeoutException):
        raise DecisionModelError(
            "timeout", f"decision model timed out after {endpoint.timeout_seconds}s"
        ) from None
    except (httpx.HTTPError, UnicodeError) as e:
        # Type name only: transport and codec errors quote the offending
        # header value, which is the key.
        raise DecisionModelError(
            "transport", f"decision model transport error: {type(e).__name__}"
        ) from None
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if response.status_code != 200:
        if response.status_code == 401 and endpoint.on_rejected is not None:
            endpoint.on_rejected()
        excerpt = response.text[:_ERROR_BODY_EXCERPT_CHARS]
        raise DecisionModelError(
            f"http_{response.status_code}",
            _scrub(f"decision model returned HTTP {response.status_code}: {excerpt}", endpoint),
        )

    try:
        payload = json.loads(response.content, parse_constant=_reject_constant)
    except ValueError:
        raise DecisionModelError("malformed", "decision model returned a non-JSON body") from None

    scores = _parse_scores(payload, keys)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    model = payload.get("model")
    return DecisionResult(
        scores=scores,
        model=model if isinstance(model, str) and model else endpoint.model,
        input_tokens=_token_count(usage.get("input_tokens")),
        output_tokens=_token_count(usage.get("output_tokens")),
        elapsed_ms=elapsed_ms,
    )


def resolve_decision_endpoint(
    *,
    base_url: str,
    platform_key: str,
    model: str,
    timeout_seconds: float,
    agent_scoped: Optional[AgentScopedKey] = None,
) -> Optional[DecisionEndpoint]:
    """The credential to ask with on THIS task, or None. The ONE place the
    credential policy lives.

    1. ``agent_scoped`` — a key issued for this turn's agent alone, when it is
       sendable. The request goes to ITS ``base_url`` (the issuer fronts the
       decision endpoint and caps that agent's spend there). Fetching it is
       the caller's job — it needs the agent id and may need a network call —
       so this function stays synchronous and vendor-free.
    2. Else ``platform_key`` (the deployment's own key), when it is sendable.
       Applies to every turn.
    3. Else the agent owner's ACTIVE helper-slot credential — the configs the
       turn's ingress installed for this task, picked with `get_helper_sdk`'s
       precedence (a subscription/CLI helper has no key to borrow; else the
       anthropic-protocol config if installed; else the openai-protocol one)
       — when it is api-key auth and its base-URL host equals ``base_url``'s
       host. The request still goes to ``base_url``; only the key is
       borrowed. Host equality is the whole rule: an internal gateway (the
       free-tier card) or any other aggregator holds a key the decision
       endpoint would reject.
    4. Else None.

    Only configs actually installed on this task count (the per-task
    snapshot), never the process-global fallback: every turn's ingress
    installs the owner's resolved configs in both cloud and local mode, so a
    task without them is not a turn, and the global holder would hand it the
    deployment's own credentials. Agent-slot credentials are not borrowed:
    the cost ledger attributes non-agent-loop rows to the helper card, so an
    agent-slot key's spend could not be booked to the card that paid.
    """
    if agent_scoped is not None and is_sendable_key(agent_scoped.api_key):
        return DecisionEndpoint(
            agent_scoped.base_url, agent_scoped.api_key, model, timeout_seconds,
            key_source=KEY_SOURCE_AGENT_SCOPED, on_rejected=agent_scoped.on_rejected,
        )
    if is_sendable_key(platform_key):
        return DecisionEndpoint(base_url, platform_key, model, timeout_seconds)
    target_host = urlparse(base_url).hostname
    if not target_host:
        return None

    from narranexus.platform.agent_framework.api_config import (
        AnthropicHelperConfig,
        OpenAIConfig,
        snapshot_user_config,
    )

    installed = snapshot_user_config()
    if installed["cli_helper"] is not None:
        return None
    helper = installed["anthropic_helper"] or installed["openai"]
    if (
        isinstance(helper, (OpenAIConfig, AnthropicHelperConfig))
        and getattr(helper, "auth_type", "api_key") in _API_KEY_AUTH_TYPES
        and is_sendable_key(helper.api_key)
        and urlparse(helper.base_url).hostname == target_host
    ):
        return DecisionEndpoint(
            base_url, helper.api_key, model, timeout_seconds,
            key_source=KEY_SOURCE_HELPER_SLOT,
        )
    return None


def _parse_scores(payload: Any, keys: Sequence[str]) -> Dict[str, float]:
    """Exactly the requested keys, each a finite probability — or raise."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise DecisionModelError("malformed", "decision model response has no answers object")
    answers = payload["answers"]
    scores: Dict[str, float] = {}
    for key in keys:
        answer = answers.get(key)
        if not isinstance(answer, dict):
            raise DecisionModelError("malformed", f"decision model gave no answer for {key!r}")
        value = answer.get(_QUESTION_TYPE)
        # bool is an int subclass; True is not a probability.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionModelError("malformed", f"answer for {key!r} is not a number")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise DecisionModelError("malformed", f"answer for {key!r} is outside [0, 1]")
        scores[key] = value
    return scores


def _reject_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON constant {name}")


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _scrub(message: str, endpoint: DecisionEndpoint) -> str:
    """Remove the key from server-provided text, in case it was echoed."""
    return message.replace(endpoint.api_key, "***") if endpoint.api_key else message


__all__ = [
    "KEY_SOURCE_AGENT_SCOPED",
    "KEY_SOURCE_HELPER_SLOT",
    "KEY_SOURCE_PLATFORM",
    "AgentScopedKey",
    "DecisionEndpoint",
    "DecisionModelError",
    "DecisionResult",
    "YesNoQuestion",
    "ask_yes_no",
    "encode_questions",
    "is_sendable_key",
    "resolve_decision_endpoint",
    "serialise",
]
