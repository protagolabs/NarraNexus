"""
@file_name: test_decision_model.py
@date: 2026-11-25
@description: The decision-model client — one request, one probability per
yes/no question, and nothing that is not a probability ever comes back.

The client is the only thing between narrative routing and a remote model, and
routing treats every exception it raises as "keep the turn where it was". So
the contract tested here is narrow and strict: a well-formed 200 yields exactly
the requested keys with numbers in [0, 1]; EVERY other shape — a missing key,
a string, 1.2, NaN, a 429, a 500, a non-JSON body, a timeout, a transport
error — raises DecisionModelError. A guessed score would be indistinguishable,
downstream, from a confident routing decision.

The transport is faked with httpx.MockTransport (house pattern); the client
under test is the real one.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from loguru import logger

from narranexus.platform.agent_framework.llm import decision_model as dm
from narranexus.platform.agent_framework.llm.decision_model import (
    DecisionEndpoint,
    DecisionModelError,
    YesNoQuestion,
    ask_yes_no,
)

SECRET = "sk-decision-secret-value-123"
BASE = "https://decisions.example.test/inference-api/openai/v1"


def _endpoint(**overrides) -> DecisionEndpoint:
    values = dict(
        base_url=BASE, api_key=SECRET, model="typesafe/jev-1.13",
        timeout_seconds=3.0,
    )
    values.update(overrides)
    return DecisionEndpoint(**values)


QUESTIONS = (
    YesNoQuestion(key="anchor", instructions={"question": "same thread?"}),
    YesNoQuestion(key="no_topic", instructions="Is it only a greeting?"),
)


def _ok_body(answers: dict) -> dict:
    return {
        "answers": {
            k: {"type": "noul", "noul": v} for k, v in answers.items()
        },
        "id": "dec_1",
        "model": "typesafe/jev-1.13",
        "usage": {"input_tokens": 333, "output_tokens": 55},
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── success ────────────────────────────────────────────────────────────────


async def test_a_well_formed_answer_parses_into_scores_and_usage():
    async with _client(
        lambda req: httpx.Response(200, json=_ok_body({"anchor": 0.93, "no_topic": 0.04}))
    ) as client:
        result = await ask_yes_no(
            _endpoint(), {"current_message": "hi"}, QUESTIONS, client=client
        )

    assert result.scores == {"anchor": 0.93, "no_topic": 0.04}
    assert result.model == "typesafe/jev-1.13"
    assert result.input_tokens == 333
    assert result.output_tokens == 55
    assert result.elapsed_ms >= 0


async def test_the_request_is_exactly_the_wire_contract():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["ctype"] = req.headers.get("content-type")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_ok_body({"anchor": 0.5, "no_topic": 0.5}))

    state = {"current_message": "and step two — déjà vu?", "previous_turn": None}
    async with _client(handler) as client:
        await ask_yes_no(_endpoint(base_url=BASE + "/"), state, QUESTIONS, client=client)

    assert seen["method"] == "POST"
    assert seen["url"] == BASE + "/alpha/decisions", "one slash, no doubled path"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["ctype"] == "application/json"
    assert seen["body"] == {
        "model": "typesafe/jev-1.13",
        "state": state,
        "questions": {
            "anchor": {"type": "noul", "instructions": {"question": "same thread?"}},
            "no_topic": {"type": "noul", "instructions": "Is it only a greeting?"},
        },
    }


async def test_boundary_probabilities_are_valid_answers():
    async with _client(
        lambda req: httpx.Response(200, json=_ok_body({"anchor": 0, "no_topic": 1}))
    ) as client:
        result = await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)
    assert result.scores == {"anchor": 0.0, "no_topic": 1.0}


async def test_extra_answers_are_ignored_not_returned():
    body = _ok_body({"anchor": 0.2, "no_topic": 0.3, "stray": 0.9})
    async with _client(lambda req: httpx.Response(200, json=body)) as client:
        result = await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)
    assert set(result.scores) == {"anchor", "no_topic"}


async def test_missing_usage_reads_as_zero_tokens_not_a_failure():
    body = _ok_body({"anchor": 0.2, "no_topic": 0.3})
    del body["usage"]
    async with _client(lambda req: httpx.Response(200, json=body)) as client:
        result = await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)
    assert (result.input_tokens, result.output_tokens) == (0, 0)


async def test_without_an_injected_client_the_call_uses_a_short_lived_one(monkeypatch):
    """The production path (no client passed) must go over the same wire."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=_ok_body({"anchor": 0.6, "no_topic": 0.1}))
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        dm.httpx, "AsyncClient",
        lambda *a, **k: real_client(transport=transport, **{
            k2: v for k2, v in k.items() if k2 != "transport"
        }),
    )
    result = await ask_yes_no(_endpoint(), {}, QUESTIONS)
    assert result.scores["anchor"] == 0.6


# ── every failure is DecisionModelError ────────────────────────────────────


@pytest.mark.parametrize(
    "answers",
    [
        {"anchor": 0.5},                                   # a key is missing
        {"anchor": 1.2, "no_topic": 0.1},                  # above 1
        {"anchor": -0.01, "no_topic": 0.1},                # below 0
        {"anchor": "0.5", "no_topic": 0.1},                # not a number
        {"anchor": True, "no_topic": 0.1},                 # bool is not a probability
        {"anchor": None, "no_topic": 0.1},                 # null
    ],
    ids=["missing", "above_one", "below_zero", "string", "bool", "null"],
)
async def test_an_unusable_answer_raises(answers):
    async with _client(lambda req: httpx.Response(200, json=_ok_body(answers))) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)


async def test_nan_raises():
    raw = (
        '{"answers": {"anchor": {"type": "noul", "noul": NaN}, '
        '"no_topic": {"type": "noul", "noul": 0.1}}}'
    )
    async with _client(lambda req: httpx.Response(200, content=raw.encode())) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)


@pytest.mark.parametrize(
    "body",
    [
        {"answers": "nope"},
        {"no_answers": {}},
        {"answers": {"anchor": 0.5, "no_topic": 0.5}},     # not the typed shape
        [1, 2, 3],
    ],
    ids=["answers_not_object", "answers_absent", "answers_untyped", "top_level_array"],
)
async def test_a_malformed_envelope_raises(body):
    async with _client(lambda req: httpx.Response(200, json=body)) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)


@pytest.mark.parametrize(
    "status,body",
    [
        (429, {"detail": "Rate limit exceeded"}),
        (500, {"detail": "Internal Server Error"}),
        (403, {"detail": "invalid token"}),
        (404, {"detail": "model not found"}),
    ],
)
async def test_a_non_200_raises_with_the_status(status, body):
    async with _client(lambda req: httpx.Response(status, json=body)) as client:
        with pytest.raises(DecisionModelError) as exc:
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)
    assert str(status) in str(exc.value)


async def test_a_non_json_body_raises():
    async with _client(
        lambda req: httpx.Response(200, content=b"<html>gateway</html>")
    ) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)


async def test_the_total_timeout_is_hard_and_raises():
    async def slow(req: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2.0)
        return httpx.Response(200, json=_ok_body({"anchor": 0.5, "no_topic": 0.5}))

    async with _client(slow) as client:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(DecisionModelError) as exc:
            await ask_yes_no(_endpoint(timeout_seconds=0.1), {}, QUESTIONS, client=client)
        waited = loop.time() - started
    assert waited < 1.0, "the timeout is a hard wall, not a per-phase hint"
    assert "timed out" in str(exc.value)


async def test_a_transport_error_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    async with _client(handler) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)


async def test_no_retry_on_failure():
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"detail": "Internal Server Error"})

    async with _client(handler) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)
    assert len(calls) == 1, "speed first: a failed decision is not retried"


async def test_an_unconfigured_endpoint_refuses_before_any_request():
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_ok_body({"anchor": 0.5, "no_topic": 0.5}))

    async with _client(handler) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(api_key=""), {}, QUESTIONS, client=client)
    assert calls == []


@pytest.mark.parametrize(
    "questions",
    [
        (),
        (YesNoQuestion("a", "x"), YesNoQuestion("a", "y")),
        (YesNoQuestion("", "x"),),
    ],
    ids=["empty", "duplicate_key", "blank_key"],
)
async def test_a_malformed_question_set_refuses_before_any_request(questions):
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_ok_body({}))

    async with _client(handler) as client:
        with pytest.raises(DecisionModelError):
            await ask_yes_no(_endpoint(), {}, questions, client=client)
    assert calls == []


def test_endpoint_configured_requires_key_base_and_model():
    assert _endpoint().configured
    assert not _endpoint(api_key="").configured
    assert not _endpoint(api_key="   ").configured
    assert not _endpoint(base_url="").configured
    assert not _endpoint(model="").configured


# ── the key never leaks ────────────────────────────────────────────────────


async def test_the_key_never_appears_in_errors_or_logs():
    """Every failure path, with a body that even ECHOES the key back."""
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        handlers = [
            lambda req: httpx.Response(403, json={"detail": f"bad token {SECRET}"}),
            lambda req: httpx.Response(200, content=f"not json {SECRET}".encode()),
            lambda req: httpx.Response(200, json={"answers": {"anchor": SECRET}}),
        ]

        def raising(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"refused for {SECRET}", request=req)

        handlers.append(raising)
        messages = []
        for handler in handlers:
            async with _client(handler) as client:
                with pytest.raises(DecisionModelError) as exc:
                    await ask_yes_no(_endpoint(), {}, QUESTIONS, client=client)
            messages.append(str(exc.value))
            messages.append(repr(exc.value))
            cause = exc.value.__cause__
            assert cause is None, "the raw exception (which may carry the key) is not chained"
    finally:
        logger.remove(sink)

    assert all(SECRET not in m for m in messages)
    assert all(SECRET not in line for line in lines)
    assert SECRET not in repr(_endpoint()), "the endpoint's repr hides the key"


# ── per-turn credential resolution ─────────────────────────────────────────
#
# Order: platform key (env) > the current user's own provider credential whose
# base-URL host equals the decision base's host > nothing. Each case runs in
# its own task so the provider ContextVars it installs cannot leak.

from narranexus.platform.agent_framework.api_config import (  # noqa: E402
    AnthropicHelperConfig,
    ClaudeConfig,
    CodexConfig,
    CliHelperConfig,
    OpenAIConfig,
    set_user_config,
)
from narranexus.platform.agent_framework.llm.decision_model import (  # noqa: E402
    resolve_decision_endpoint,
)

DECISION_BASE = "https://api.netmind.ai/inference-api/openai/v1"


def _install(
    *,
    openai: OpenAIConfig | None = None,
    claude: ClaudeConfig | None = None,
    codex: CodexConfig | None = None,
    anthropic_helper: AnthropicHelperConfig | None = None,
) -> None:
    set_user_config(
        claude or ClaudeConfig(),
        openai or OpenAIConfig(),
        codex or CodexConfig(),
        anthropic_helper or AnthropicHelperConfig(),
        CliHelperConfig(),
    )


async def _resolve_in_task(platform_key: str = "", **providers):
    async def run():
        _install(**providers)
        return resolve_decision_endpoint(
            base_url=DECISION_BASE, platform_key=platform_key,
            model="typesafe/jev-1.13", timeout_seconds=3.0,
        )

    return await asyncio.create_task(run())


async def test_the_platform_key_wins_over_a_qualifying_user_key():
    ep = await _resolve_in_task(
        platform_key="platform-key",
        openai=OpenAIConfig(api_key="user-key", base_url=DECISION_BASE),
    )
    assert ep is not None and ep.api_key == "platform-key"
    assert ep.base_url == DECISION_BASE


async def test_a_user_credential_on_the_decision_host_qualifies_with_its_own_key():
    ep = await _resolve_in_task(
        openai=OpenAIConfig(api_key="user-key", base_url=DECISION_BASE),
    )
    assert ep is not None and ep.api_key == "user-key"
    assert ep.base_url == DECISION_BASE, "always the decision base, not the user's path"


async def test_same_host_different_path_qualifies():
    """The same vendor's other-protocol endpoint (one key, one host)."""
    ep = await _resolve_in_task(
        anthropic_helper=AnthropicHelperConfig(
            api_key="anthropic-proto-key",
            base_url="https://api.netmind.ai/inference-api/anthropic",
        ),
    )
    assert ep is not None and ep.api_key == "anthropic-proto-key"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://llm-gateway:4000/v1",          # cloud free-tier internal gateway
        "http://litellm:4000",                 # local gateway
        "https://openrouter.ai/api/v1",        # another aggregator
        "https://api.netmind.ai.evil.example/v1",  # suffix trick
        "",                                    # official OpenAI (empty base)
    ],
)
async def test_a_user_credential_on_another_host_does_not_qualify(base_url):
    ep = await _resolve_in_task(
        openai=OpenAIConfig(api_key="virtual-key", base_url=base_url),
    )
    assert ep is None


async def test_no_credential_at_all_resolves_to_none():
    assert await _resolve_in_task() is None


async def test_a_qualifying_host_with_an_empty_key_does_not_qualify():
    ep = await _resolve_in_task(openai=OpenAIConfig(api_key="", base_url=DECISION_BASE))
    assert ep is None


async def test_an_oauth_agent_credential_does_not_qualify():
    ep = await _resolve_in_task(
        claude=ClaudeConfig(api_key="oauth-token", base_url=DECISION_BASE, auth_type="oauth"),
    )
    assert ep is None


async def test_the_first_qualifying_slot_wins_in_the_documented_order():
    """helper openai -> helper anthropic -> agent claude -> agent codex."""
    ep = await _resolve_in_task(
        openai=OpenAIConfig(api_key="helper-openai", base_url=DECISION_BASE),
        anthropic_helper=AnthropicHelperConfig(
            api_key="helper-anthropic",
            base_url="https://api.netmind.ai/inference-api/anthropic",
        ),
        claude=ClaudeConfig(api_key="agent-claude", base_url=DECISION_BASE),
        codex=CodexConfig(api_key="agent-codex", base_url=DECISION_BASE, auth_type="api_key"),
    )
    assert ep is not None and ep.api_key == "helper-openai"

    ep = await _resolve_in_task(
        anthropic_helper=AnthropicHelperConfig(
            api_key="helper-anthropic",
            base_url="https://api.netmind.ai/inference-api/anthropic",
        ),
        claude=ClaudeConfig(api_key="agent-claude", base_url=DECISION_BASE),
    )
    assert ep is not None and ep.api_key == "helper-anthropic"

    ep = await _resolve_in_task(
        codex=CodexConfig(api_key="agent-codex", base_url=DECISION_BASE, auth_type="api_key"),
    )
    assert ep is not None and ep.api_key == "agent-codex"


async def test_an_unparseable_decision_base_resolves_to_none_for_user_keys():
    async def run():
        _install(openai=OpenAIConfig(api_key="user-key", base_url=DECISION_BASE))
        return resolve_decision_endpoint(
            base_url="not a url", platform_key="", model="m", timeout_seconds=3.0,
        )

    assert await asyncio.create_task(run()) is None
