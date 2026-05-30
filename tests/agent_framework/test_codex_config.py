"""
@file_name: test_codex_config.py
@date: 2026-05-29
@description: Unit tests for CodexConfig + codex_config ContextVar
proxy. Mirrors test_api_config_context_vars.py shape.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.api_config import (
    CodexConfig,
    codex_config,
    _codex_ctx,
)


# ---------------- CodexConfig.to_cli_env ---------------------------


def test_default_blanks_codex_api_key():
    """Empty config explicitly blanks the env var (anti-leak)."""
    env = CodexConfig().to_cli_env()
    assert env == {"CODEX_API_KEY": ""}


def test_api_key_injected_in_api_key_mode():
    env = CodexConfig(api_key="sk-xxx", auth_type="api_key").to_cli_env()
    assert env == {"CODEX_API_KEY": "sk-xxx"}


def test_oauth_mode_does_not_inject_api_key():
    """auth_type='oauth' → fall back to ~/.codex/auth.json, NOT env."""
    env = CodexConfig(api_key="ignored", auth_type="oauth").to_cli_env()
    assert env == {"CODEX_API_KEY": ""}


def test_base_url_and_model_not_in_env():
    """base_url + model flow via config.toml, NOT env vars."""
    env = CodexConfig(
        api_key="k", auth_type="api_key",
        base_url="https://api.example.com", model="gpt-5.4-codex",
    ).to_cli_env()
    assert "CODEX_BASE_URL" not in env
    assert "CODEX_MODEL" not in env
    # Only CODEX_API_KEY is set
    assert set(env.keys()) == {"CODEX_API_KEY"}


def test_dataclass_is_frozen():
    """CodexConfig is intentionally frozen so two tasks can hold
    different snapshots without mutation hazards."""
    c = CodexConfig(api_key="k")
    with pytest.raises(Exception):  # FrozenInstanceError on dataclass
        c.api_key = "different"  # type: ignore[misc]


# ---------------- ContextVar proxy --------------------------------


def test_codex_config_proxy_returns_holder_default_when_ctx_unset():
    """When the ContextVar is None, the proxy falls back to the
    _holder._codex slot which is initialised to an empty CodexConfig."""
    _codex_ctx.set(None)
    # Reading any attribute should not raise
    assert codex_config.model == ""
    assert codex_config.api_key == ""


def test_codex_config_proxy_reads_ctxvar_override():
    """When the ContextVar carries a CodexConfig, proxy reads from it."""
    override = CodexConfig(model="gpt-5.4-codex", api_key="sk-override")
    token = _codex_ctx.set(override)
    try:
        assert codex_config.model == "gpt-5.4-codex"
        assert codex_config.api_key == "sk-override"
    finally:
        _codex_ctx.reset(token)
