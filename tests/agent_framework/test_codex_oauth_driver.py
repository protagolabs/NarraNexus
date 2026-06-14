"""
@file_name: test_codex_oauth_driver.py
@date: 2026-05-29
@description: Tests for CodexOAuthDriver registration + probe.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from xyz_agent_context.agent_framework.provider_driver.base import ProviderCard
from xyz_agent_context.agent_framework.provider_driver.drivers.codex_oauth import (
    CodexOAuthDriver,
)
from xyz_agent_context.agent_framework.provider_driver.registry import DRIVER_REGISTRY


def _stub_card(auth_ref: str | None = "codex-cli:~/.codex/auth.json") -> ProviderCard:
    return ProviderCard(
        provider_id="p1",
        user_id="u1",
        name="codex",
        source="codex_oauth",
        protocol="openai",
        auth_type="oauth",
        api_key="",
        base_url="",
        auth_ref=auth_ref,
        driver_type="codex_oauth",
    )


def test_driver_registered_under_codex_oauth_key():
    assert "codex_oauth" in DRIVER_REGISTRY
    assert DRIVER_REGISTRY["codex_oauth"] is CodexOAuthDriver


def test_driver_type_classmethod():
    assert CodexOAuthDriver.driver_type() == "codex_oauth"


@pytest.mark.asyncio
async def test_probe_returns_ok_when_credentials_file_exists(tmp_path, monkeypatch):
    """When ~/.codex/auth.json exists, probe reports ok=True."""
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    health = await CodexOAuthDriver(_stub_card()).probe()
    assert health.ok is True
    assert "credentials present" in health.detail


@pytest.mark.asyncio
async def test_probe_returns_not_ok_when_credentials_missing(tmp_path, monkeypatch):
    """Empty $CODEX_HOME → no auth.json → probe reports ok=False with hint."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    health = await CodexOAuthDriver(_stub_card()).probe()
    assert health.ok is False
    assert "codex login" in health.detail.lower()


@pytest.mark.asyncio
async def test_probe_returns_not_ok_when_auth_ref_missing():
    """A card with no auth_ref is malformed — probe surfaces that."""
    card = _stub_card(auth_ref=None)
    health = await CodexOAuthDriver(card).probe()
    assert health.ok is False
    assert "auth_ref" in health.detail


@pytest.mark.asyncio
async def test_probe_returns_not_ok_when_auth_ref_wrong_scheme():
    """An auth_ref that doesn't start with 'codex-cli:' is wrong driver."""
    card = _stub_card(auth_ref="claude-cli:~/.claude/.credentials.json")
    health = await CodexOAuthDriver(card).probe()
    assert health.ok is False


@pytest.mark.asyncio
async def test_probe_returns_not_ok_when_path_is_directory(tmp_path, monkeypatch):
    """Auth file MUST be a regular file, not a directory."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").mkdir()  # directory, not file
    health = await CodexOAuthDriver(_stub_card()).probe()
    assert health.ok is False
    assert "not a file" in health.detail


def test_build_claude_config_raises_not_implemented():
    """Codex driver doesn't fit the agent-slot ClaudeConfig shape."""
    d = CodexOAuthDriver(_stub_card())
    with pytest.raises(NotImplementedError):
        d.build_claude_config(model="gpt-5.4-codex")


def test_build_openai_config_raises_not_implemented():
    """Codex driver doesn't serve the helper_llm slot either."""
    d = CodexOAuthDriver(_stub_card())
    with pytest.raises(NotImplementedError):
        d.build_openai_config(model="gpt-5.4-codex")
