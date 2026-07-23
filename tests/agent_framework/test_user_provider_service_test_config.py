"""
@file_name: test_user_provider_service_test_config.py
@date: 2026-07-23
@description: Tests for UserProviderService.test_provider_config — the
STATELESS twin of test_provider(). Lets the add-provider form verify
connectivity from raw form values BEFORE anything is saved, so a wrong
key / base_url / model never pollutes the stored config first.

Contract under test:
- A valid card_type builds a transient ProviderConfig (protocol /
  auth_type / models faithfully mapped) and delegates to
  provider_registry.test_provider, passing its (ok, msg) straight
  through — WITHOUT reading or writing the DB.
- An unsupported card_type short-circuits to (False, ...) and never
  touches the registry.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework import provider_registry as registry_mod
from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    ProviderProtocol,
    ProviderSource,
)


class _ExplodingDB:
    """Any DB access is a bug: test_provider_config must be stateless."""

    async def get(self, *a, **k):  # pragma: no cover - guard
        raise AssertionError("test_provider_config must not read the DB")

    async def get_one(self, *a, **k):  # pragma: no cover - guard
        raise AssertionError("test_provider_config must not read the DB")

    async def insert(self, *a, **k):  # pragma: no cover - guard
        raise AssertionError("test_provider_config must not write the DB")

    async def update(self, *a, **k):  # pragma: no cover - guard
        raise AssertionError("test_provider_config must not write the DB")

    async def delete(self, *a, **k):  # pragma: no cover - guard
        raise AssertionError("test_provider_config must not write the DB")


@pytest.mark.asyncio
async def test_test_provider_config_delegates_to_registry(monkeypatch):
    captured = {}

    async def _fake_test(prov):
        captured["prov"] = prov
        return True, "Connected successfully"

    monkeypatch.setattr(
        registry_mod.provider_registry, "test_provider", _fake_test
    )

    svc = UserProviderService(_ExplodingDB())
    ok, msg = await svc.test_provider_config(
        card_type="openai",
        api_key="sk-test",
        base_url="https://proxy.example/v1",
        auth_type="api_key",
        models=["gpt-x", "gpt-y"],
    )

    assert ok is True
    assert msg == "Connected successfully"
    prov = captured["prov"]
    assert prov.protocol == ProviderProtocol.OPENAI
    assert prov.auth_type == AuthType.API_KEY
    assert prov.source == ProviderSource.USER
    assert prov.api_key == "sk-test"
    assert prov.base_url == "https://proxy.example/v1"
    assert prov.models == ["gpt-x", "gpt-y"]


@pytest.mark.asyncio
async def test_test_provider_config_maps_anthropic_bearer(monkeypatch):
    captured = {}

    async def _fake_test(prov):
        captured["prov"] = prov
        return False, "Authentication failed (invalid API key)"

    monkeypatch.setattr(
        registry_mod.provider_registry, "test_provider", _fake_test
    )

    svc = UserProviderService(_ExplodingDB())
    ok, msg = await svc.test_provider_config(
        card_type="anthropic",
        api_key="bad",
        auth_type="bearer_token",
    )

    assert ok is False
    assert "Authentication failed" in msg
    prov = captured["prov"]
    assert prov.protocol == ProviderProtocol.ANTHROPIC
    assert prov.auth_type == AuthType.BEARER_TOKEN
    assert prov.models == []


@pytest.mark.asyncio
async def test_test_provider_config_rejects_unknown_card_type(monkeypatch):
    async def _boom(prov):  # pragma: no cover - must not be reached
        raise AssertionError("registry must not be called for bad card_type")

    monkeypatch.setattr(
        registry_mod.provider_registry, "test_provider", _boom
    )

    svc = UserProviderService(_ExplodingDB())
    ok, msg = await svc.test_provider_config(card_type="gemini", api_key="k")

    assert ok is False
    assert "gemini" in msg
