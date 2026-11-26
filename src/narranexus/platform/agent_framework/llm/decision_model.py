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

The key goes only into the Authorization header; error text is built here and
scrubbed of it, and raw exceptions are not chained.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx

#: The only question type the client asks today: "the probability of yes".
_QUESTION_TYPE = "noul"
_DECISIONS_PATH = "/alpha/decisions"
#: OAuth tokens are bound to their issuer and never valid elsewhere.
_API_KEY_AUTH_TYPES = frozenset({"api_key", "bearer_token"})
#: How much of an error body is quoted into the exception — enough for
#: `{"detail":"Rate limit exceeded"}`, short enough not to dump a gateway page.
_ERROR_BODY_EXCERPT_CHARS = 200


class DecisionModelError(Exception):
    """The decision model produced no usable answer. Carries no score."""


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
    """Where and how to ask. Owned by the caller's configuration."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        """True when a request could be sent at all (base, key and model set)."""
        return bool(
            self.base_url.strip() and self.api_key.strip() and self.model.strip()
        )


def serialise(value: Any) -> str:
    """The one JSON encoding the request body uses.

    Public so a caller that records "how much did we send" counts the same
    characters that go on the wire. ``allow_nan=False``: a NaN in a state is a
    caller bug, and JSON has no spelling for it.
    """
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

    Raises `DecisionModelError` on any failure — see the module docstring.
    ``client`` is injectable for tests; production passes none and gets a
    short-lived client per call (the house pattern for outbound calls).
    """
    if not endpoint.configured:
        raise DecisionModelError("decision model endpoint is not configured")
    keys = [q.key for q in questions]
    if not keys:
        raise DecisionModelError("no questions to ask")
    if any(not k for k in keys) or len(set(keys)) != len(keys):
        raise DecisionModelError("question keys must be non-empty and unique")

    try:
        body = serialise(
            {
                "model": endpoint.model,
                "state": state,
                "questions": encode_questions(questions),
            }
        )
    except (TypeError, ValueError) as e:
        raise DecisionModelError(
            f"request is not JSON-serialisable: {type(e).__name__}"
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
            f"decision model timed out after {endpoint.timeout_seconds}s"
        ) from None
    except httpx.HTTPError as e:
        raise DecisionModelError(
            _scrub(f"decision model transport error: {type(e).__name__}: {e}", endpoint)
        ) from None
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if response.status_code != 200:
        excerpt = response.text[:_ERROR_BODY_EXCERPT_CHARS]
        raise DecisionModelError(
            _scrub(
                f"decision model returned HTTP {response.status_code}: {excerpt}",
                endpoint,
            )
        )

    try:
        payload = json.loads(response.content, parse_constant=_reject_constant)
    except ValueError:
        raise DecisionModelError("decision model returned a non-JSON body") from None

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
    *, base_url: str, platform_key: str, model: str, timeout_seconds: float
) -> Optional[DecisionEndpoint]:
    """The credential to ask with on THIS task, or None.

    1. ``platform_key`` (the deployment's own key), when set.
    2. Else the current user's own provider credential, if it can reach
       ``base_url``: the first api-key credential whose base-URL host equals
       ``base_url``'s host, checked in the order helper slot (openai protocol,
       then anthropic protocol), then agent slot (claude, then codex). The
       request still goes to ``base_url`` — only the key is borrowed.
       Host equality is the whole rule: an internal gateway or any other
       aggregator holds a key the decision endpoint would reject.
    3. Else None.

    Reads the provider configs the runtime resolver installed for this task
    (the same per-task ContextVars, with the same global fallback, the helper
    SDK reads), so concurrent turns of different users see their own keys.
    """
    if platform_key.strip():
        return DecisionEndpoint(base_url, platform_key, model, timeout_seconds)
    target_host = urlparse(base_url).hostname
    if not target_host:
        return None
    for user_base, user_key in _user_api_key_credentials():
        if user_key.strip() and urlparse(user_base).hostname == target_host:
            return DecisionEndpoint(base_url, user_key, model, timeout_seconds)
    return None


def _user_api_key_credentials() -> Iterator[Tuple[str, str]]:
    """(base_url, key) of the current task's api-key provider configs, in the
    resolution order documented on `resolve_decision_endpoint`."""
    from narranexus.platform.agent_framework.api_config import (
        anthropic_helper_config,
        claude_config,
        codex_config,
        openai_config,
    )

    yield openai_config.base_url, openai_config.api_key
    if anthropic_helper_config.auth_type in _API_KEY_AUTH_TYPES:
        yield anthropic_helper_config.base_url, anthropic_helper_config.api_key
    if claude_config.auth_type in _API_KEY_AUTH_TYPES:
        yield claude_config.base_url, claude_config.api_key
    if codex_config.auth_type in _API_KEY_AUTH_TYPES:
        yield codex_config.base_url, codex_config.api_key


def _parse_scores(payload: Any, keys: Sequence[str]) -> Dict[str, float]:
    """Exactly the requested keys, each a finite probability — or raise."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise DecisionModelError("decision model response has no answers object")
    answers = payload["answers"]
    scores: Dict[str, float] = {}
    for key in keys:
        answer = answers.get(key)
        if not isinstance(answer, dict):
            raise DecisionModelError(f"decision model gave no answer for {key!r}")
        value = answer.get(_QUESTION_TYPE)
        # bool is an int subclass; True is not a probability.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionModelError(f"answer for {key!r} is not a number")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise DecisionModelError(f"answer for {key!r} is outside [0, 1]")
        scores[key] = value
    return scores


def _reject_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON constant {name}")


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _scrub(message: str, endpoint: DecisionEndpoint) -> str:
    """Remove the key from a message, in case a server or library echoed it."""
    key = endpoint.api_key
    return message.replace(key, "***") if key else message


__all__ = [
    "DecisionEndpoint",
    "DecisionModelError",
    "DecisionResult",
    "YesNoQuestion",
    "ask_yes_no",
    "encode_questions",
    "resolve_decision_endpoint",
    "serialise",
]
