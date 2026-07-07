"""
@file_name: test_cli_helper.py
@date: 2026-07-07
@description: Tests for the CLI-backed helper LLM (subscription covers helper).

Covers: get_helper_sdk dispatch precedence (cli > anthropic > openai), the
CLI-helper driver configs, the resolver routing an OAuth helper slot to the
CLI helper, and CliHelperSDK.llm_function's shared parse/cost/wrap logic.
"""
from __future__ import annotations

import contextvars

import pytest
from pydantic import BaseModel

from xyz_agent_context.agent_framework import api_config as ac
from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.anthropic_helper_sdk import AnthropicHelperSDK
from xyz_agent_context.agent_framework.cli_helper_sdk import CliHelperSDK
from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK


def _run_isolated(fn):
    return contextvars.copy_context().run(fn)


# ---------------------------------------------------------------------------
# Dispatch precedence
# ---------------------------------------------------------------------------

def test_dispatch_cli_when_cli_helper_set():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code"))
        return type(get_helper_sdk())
    assert _run_isolated(check) is CliHelperSDK


def test_dispatch_cli_wins_over_anthropic():
    def check():
        set_user_config(
            ClaudeConfig(), OpenAIConfig(),
            anthropic_helper=AnthropicHelperConfig(api_key="k"),
            cli_helper=CliHelperConfig(framework="codex_cli"),
        )
        return type(get_helper_sdk())
    assert _run_isolated(check) is CliHelperSDK


def test_dispatch_anthropic_when_only_anthropic_set():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        anthropic_helper=AnthropicHelperConfig(api_key="k"))
        return type(get_helper_sdk())
    assert _run_isolated(check) is AnthropicHelperSDK


def test_dispatch_openai_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig())
        return type(get_helper_sdk())
    assert _run_isolated(check) is OpenAIAgentsSDK


# ---------------------------------------------------------------------------
# Model resolution defaults per framework
# ---------------------------------------------------------------------------

def test_resolve_model_claude_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code", model=""))
        return CliHelperSDK._resolve_model(None)
    assert _run_isolated(check) == "haiku"


def test_resolve_model_codex_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="codex_cli", model="default"))
        return CliHelperSDK._resolve_model(None)
    assert "codex" in _run_isolated(check)


def test_resolve_model_slot_wins_over_percall():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code", model="sonnet"))
        # a per-call OpenAI-flavoured name is ignored; slot model wins
        return CliHelperSDK._resolve_model("gpt-5.4-mini")
    assert _run_isolated(check) == "sonnet"


# ---------------------------------------------------------------------------
# Driver → CliHelperConfig
# ---------------------------------------------------------------------------

def _card(**kw):
    from xyz_agent_context.agent_framework.provider_driver.base import ProviderCard
    base = dict(
        provider_id="p1", user_id="u1", name="n", source="claude_oauth",
        protocol="anthropic", auth_type="oauth", api_key="", base_url="",
        models=[], driver_type="claude_oauth", auth_ref="claude-cli:~/.claude",
    )
    base.update(kw)
    return ProviderCard(**base)


def test_claude_oauth_driver_builds_cli_helper():
    from xyz_agent_context.agent_framework.provider_driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )
    cfg = ClaudeOAuthDriver(_card()).build_cli_helper_config("haiku")
    assert isinstance(cfg, CliHelperConfig)
    assert cfg.framework == "claude_code"
    assert cfg.auth_type == "oauth"
    assert cfg.api_key == ""
    assert cfg.model == "haiku"


def test_codex_oauth_driver_builds_cli_helper():
    from xyz_agent_context.agent_framework.provider_driver.drivers.codex_oauth import (
        CodexOAuthDriver,
    )
    card = _card(source="codex_oauth", protocol="openai", driver_type="codex_oauth")
    cfg = CodexOAuthDriver(card).build_cli_helper_config("gpt-5.1-codex-mini")
    assert cfg.framework == "codex_cli"
    assert cfg.auth_type == "oauth"


# ---------------------------------------------------------------------------
# Resolver routes an OAuth helper slot to the CLI helper
# ---------------------------------------------------------------------------

def test_resolver_routes_oauth_helper_to_cli():
    from xyz_agent_context.agent_framework.provider_driver.resolver import (
        _resolve_slot_target,
    )
    method, key = _resolve_slot_target("helper_llm", "claude_code", _card())
    assert method == "build_cli_helper_config"
    assert key == "cli_helper"


def test_resolver_apikey_helper_still_openai():
    from xyz_agent_context.agent_framework.provider_driver.resolver import (
        _resolve_slot_target,
    )
    card = _card(source="user", protocol="openai", auth_type="api_key", api_key="sk-x")
    method, key = _resolve_slot_target("helper_llm", "claude_code", card)
    assert method == "build_openai_config"


# ---------------------------------------------------------------------------
# CliHelperSDK.llm_function shared parse / wrap logic (CLI call mocked)
# ---------------------------------------------------------------------------

class _Out(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_llm_function_structured(monkeypatch):
    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return '{"answer": "hi"}', 10, 5

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)

    def call():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code"))
    contextvars.copy_context().run(call)
    # set in THIS task too so cli_helper_config resolves during the call
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="claude_code"))

    res = await sdk.llm_function("inst", "in", output_type=_Out)
    assert res.final_output.answer == "hi"


@pytest.mark.asyncio
async def test_llm_function_no_schema_returns_text(monkeypatch):
    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return "plain reply", 0, 0

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="claude_code"))
    res = await sdk.llm_function("inst", "in")
    assert res.final_output == "plain reply"


@pytest.mark.asyncio
async def test_llm_function_raises_when_no_json(monkeypatch):
    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return "sorry, I cannot", 0, 0

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="claude_code"))
    with pytest.raises(ValueError, match="Could not extract JSON"):
        await sdk.llm_function("inst", "in", output_type=_Out)
