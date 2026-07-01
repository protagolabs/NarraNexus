"""
@file_name: test_to_cli_env_model_alias.py
@author: NetMind.AI
@date: 2026-07-01
@description: Regression — ClaudeConfig.to_cli_env must NOT feed a bare CLI
family alias (opus/sonnet/haiku) into ANTHROPIC_DEFAULT_*_MODEL.

Root cause of the "Claude Code slot configured but silently falls back to the
helper LLM" bug: the Claude OAuth path uses bare CLI family aliases as the
model (see model_catalog: "opus"/"sonnet"/"haiku" are valid ONLY as the CLI
`--model` argument). to_cli_env used to pin every ANTHROPIC_DEFAULT_*_MODEL to
`self.model`, so with model="opus" it set ANTHROPIC_DEFAULT_OPUS_MODEL="opus".
That poisons the CLI's own alias→id resolution (opus → literal "opus"), the API
rejects it with invalid_request, the main loop yields no_reply, and the runtime
falls back to the helper LLM.

The override is correct ONLY for a full model id (custom / proxy providers,
e.g. "minimax/minimax-m2.5") where internal CLI calls must be pinned to the one
model the proxy serves. For a bare alias the overrides must be blank so the CLI
resolves the alias natively.
"""
import pytest

from xyz_agent_context.agent_framework.api_config import ClaudeConfig

_OVERRIDE_KEYS = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


@pytest.mark.parametrize("alias", ["opus", "sonnet", "haiku"])
def test_bare_cli_alias_blanks_overrides(alias):
    """A bare CLI family alias must leave every DEFAULT_*_MODEL override blank
    so Claude Code resolves the alias into a concrete model id itself."""
    env = ClaudeConfig(model=alias, auth_type="oauth").to_cli_env()
    for key in _OVERRIDE_KEYS:
        assert env[key] == "", f"{key} should be blank for bare alias {alias!r}, got {env[key]!r}"


def test_full_model_id_pins_overrides():
    """A full model id (custom / proxy provider) must pin every internal CLI
    call to that same model — otherwise WebFetch/subagent calls drift to
    official Anthropic ids the proxy does not serve."""
    model = "minimax/minimax-m2.5"
    env = ClaudeConfig(model=model, api_key="k", base_url="https://api.netmind.ai").to_cli_env()
    for key in _OVERRIDE_KEYS:
        assert env[key] == model, f"{key} should be pinned to {model!r}, got {env[key]!r}"


def test_full_claude_id_pins_overrides():
    """A fully-qualified claude id is NOT a bare alias → it must still pin
    (only the three unqualified aliases are special)."""
    model = "claude-opus-4-8"
    env = ClaudeConfig(model=model, auth_type="oauth").to_cli_env()
    for key in _OVERRIDE_KEYS:
        assert env[key] == model


def test_empty_model_blanks_overrides():
    """No configured model → blank the overrides so a stale inherited value
    cannot steer this run (tenant isolation)."""
    env = ClaudeConfig(model="").to_cli_env()
    for key in _OVERRIDE_KEYS:
        assert env[key] == ""
