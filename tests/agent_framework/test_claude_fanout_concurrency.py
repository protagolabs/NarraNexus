"""
@file_name: test_claude_fanout_concurrency.py
@date: 2026-09-09
@description: Bounded parallel tool execution for the Claude Code CLI on a
claude.ai subscription — the lever that caps sub-agent (Task) fan-out.

The CLI runs every concurrency-safe tool call of ONE assistant message in
parallel (parallel Read / Grep / MCP calls and every sub-agent launch alike),
bounded only by ``CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`` (default 10 inside
the binary). On a subscription each sub-agent is its own model loop against
a shared quota, and the CLI never retries a 429 for a subscriber (PR #379
handles the retry / preservation side), so an unbounded fan-out is a 429
storm. ``ClaudeConfig.to_cli_env`` — the platform's settings → CLAUDE_CODE_*
seam — now injects the cap from ``settings.claude_max_tool_use_concurrency``
for SUBSCRIPTION auth only; keyed auth keeps the CLI default (the CLI retries
its own 429s there, and the cap would only slow parallel reads down). This
is a CONCURRENCY limit, never an iteration / time ceiling on the loop
(binding rule #14): every call still runs, later.

Each test goes red when the injection is removed: the assertions are on the
env the SDK options carry into the subprocess, driven through the real
adapter so the seam is exercised where production uses it.
"""
from __future__ import annotations

import pytest

import narranexus_plugins.frameworks_claude_code.sdk as sdk_mod
from narranexus_plugins.frameworks_claude_code.sdk import ClaudeAgentSDK
from narranexus.platform.agent_framework.api_config import (
    CLI_MAX_TOOL_USE_CONCURRENCY_ENV,
    SUBSCRIPTION_AUTH_TYPES,
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)
from narranexus.platform.settings import settings

from tests.agent_framework.test_claude_sdk_resume import ResultMessage, _StubClient


@pytest.fixture(autouse=True)
def _stub_transport(monkeypatch):
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    yield


def _configure(auth_type: str) -> None:
    set_user_config(
        claude=ClaudeConfig(api_key="k", auth_type=auth_type),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )


async def _cli_env(auth_type: str) -> dict:
    _configure(auth_type)
    sdk = ClaudeAgentSDK(working_path="/tmp/ws-fanout")
    _ = [e async for e in sdk.agent_loop(
        [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "fan out"},
        ],
        {},
    )]
    (client,) = _StubClient.instances
    return client.options.env


def test_env_name_is_the_cli_knob():
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV == "CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY"


def test_subscription_set_is_the_cli_definition():
    assert SUBSCRIPTION_AUTH_TYPES == frozenset({"oauth", "oauth_token"})
    assert "api_key" not in SUBSCRIPTION_AUTH_TYPES


def test_default_setting_is_a_bounded_positive_cap():
    assert isinstance(settings.claude_max_tool_use_concurrency, int)
    assert 0 < settings.claude_max_tool_use_concurrency < 10  # below the CLI's own 10


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", sorted(SUBSCRIPTION_AUTH_TYPES))
async def test_cap_is_injected_for_subscription_auth(monkeypatch, auth_type):
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", 3)
    env = await _cli_env(auth_type)
    assert env[CLI_MAX_TOOL_USE_CONCURRENCY_ENV] == "3"


@pytest.mark.asyncio
async def test_default_cap_reaches_the_subprocess_env_for_a_subscription():
    env = await _cli_env("oauth_token")
    assert env[CLI_MAX_TOOL_USE_CONCURRENCY_ENV] == str(
        settings.claude_max_tool_use_concurrency
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["api_key", "bearer"])
async def test_keyed_auth_keeps_the_cli_default(auth_type):
    """Allowed / passthrough case: the CLI retries its own 429s for keyed
    auth, so nothing is injected and parallel tool batches stay at 10."""
    env = await _cli_env(auth_type)
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV not in env


@pytest.mark.asyncio
async def test_zero_means_cli_default_and_injects_nothing(monkeypatch):
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", 0)
    env = await _cli_env("oauth_token")
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV not in env


@pytest.mark.asyncio
async def test_negative_is_treated_as_cli_default(monkeypatch):
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", -5)
    env = await _cli_env("oauth_token")
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV not in env
