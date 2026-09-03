"""
@file_name: test_resolve_model.py
@author: Bin Liang
@date: 2026-09-03
@description: One model-resolution rule; the three helper clients agree with it and with their pre-refactor behavior.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.llm_client import DEFAULT_MODEL_SENTINEL, resolve_helper_model


@pytest.mark.parametrize(
    ("slot", "requested", "honour", "expected"),
    [
        ("claude-sonnet", "gpt-x", False, "claude-sonnet"),
        ("claude-sonnet", "gpt-x", True, "claude-sonnet"),
        (DEFAULT_MODEL_SENTINEL, "gpt-x", True, "gpt-x"),
        (DEFAULT_MODEL_SENTINEL, "gpt-x", False, "fallback"),
        ("", "gpt-x", True, "gpt-x"),
        (None, None, True, "fallback"),
        (DEFAULT_MODEL_SENTINEL, None, True, "fallback"),
    ],
)
def test_rule_table(slot, requested, honour, expected):
    assert resolve_helper_model(slot, requested, default="fallback", honour_requested=honour) == expected


def _with_ctx(ctx_var, config, fn):
    """Install a per-task config the way the resolver does (ContextVar), never by
    monkeypatching the proxy — that would shadow its forwarding for every later test."""
    token = ctx_var.set(config)
    try:
        return fn()
    finally:
        ctx_var.reset(token)


def test_openai_helper_official_and_custom_endpoints_agree():
    """The legacy three-mode docstring collapsed to one rule: endpoint is irrelevant."""
    from xyz_agent_context.agent_framework import api_config
    from xyz_agent_context.agent_framework.adapters.openai_agents import OpenAIAgentsSDK

    for base_url in ("https://api.openai.com/v1", "https://custom.example/v1"):
        forced = api_config.OpenAIConfig(model="forced-model", base_url=base_url)
        assert _with_ctx(api_config._openai_ctx, forced, lambda: OpenAIAgentsSDK._resolve_model("gpt-4o-mini")) == "forced-model"
        default = api_config.OpenAIConfig(model="default", base_url=base_url)
        assert _with_ctx(api_config._openai_ctx, default, lambda: OpenAIAgentsSDK._resolve_model("gpt-4o-mini")) == "gpt-4o-mini"
        assert _with_ctx(api_config._openai_ctx, default, lambda: OpenAIAgentsSDK._resolve_model(None)) == api_config.OpenAIConfig.model


def test_anthropic_helper_ignores_requested_and_cli_helper_picks_framework_default():
    from xyz_agent_context.agent_framework import api_config
    from xyz_agent_context.agent_framework.llm import cli_helper
    from xyz_agent_context.agent_framework.llm.anthropic_helper import AnthropicHelperSDK
    from xyz_agent_context.agent_framework.llm.cli_helper import CliHelperSDK

    slot = api_config.AnthropicHelperConfig(model="claude-x")
    assert _with_ctx(api_config._anthropic_helper_ctx, slot, lambda: AnthropicHelperSDK._resolve_model("gpt-4o-mini")) == "claude-x"
    sentinel = api_config.AnthropicHelperConfig(model="default")
    assert _with_ctx(api_config._anthropic_helper_ctx, sentinel, lambda: AnthropicHelperSDK._resolve_model("gpt-4o-mini")) == api_config.AnthropicHelperConfig.model

    codex = api_config.CliHelperConfig(model="default", framework="codex_cli")
    assert _with_ctx(api_config._cli_helper_ctx, codex, lambda: CliHelperSDK._resolve_model("gpt-4o-mini")) == cli_helper._DEFAULT_CODEX_HELPER_MODEL
    claude = api_config.CliHelperConfig(model="default", framework="claude_code")
    assert _with_ctx(api_config._cli_helper_ctx, claude, lambda: CliHelperSDK._resolve_model("gpt-4o-mini")) == cli_helper._DEFAULT_CLAUDE_HELPER_MODEL
    forced = api_config.CliHelperConfig(model="slot-model", framework="claude_code")
    assert _with_ctx(api_config._cli_helper_ctx, forced, lambda: CliHelperSDK._resolve_model("gpt-4o-mini")) == "slot-model"
