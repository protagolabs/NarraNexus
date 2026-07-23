"""
@file_name: test_quota_route.py
@author: rujing.yan
@date: 2026-07-23
@description: Tests for the real GET /api/quota/me handler, focused on the
``free_tier`` lock block that settings panels (global Model Defaults + the
per-agent panel) read to render an honest "changes apply once your free quota is
used up" banner.

While the free tier has budget, the runtime pins runs to the fixed system model
and ignores the user's own slot edits — the block tells the UI so. Verdict comes
from the single-source predicate ``ProviderResolver.is_free_tier_active``; the
model is the system agent model surfaced while locked.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.quota as mod


def _fake_quota_row():
    """A quota row shaped for ``_quota_to_dict`` (status + token counters)."""
    return SimpleNamespace(
        status=SimpleNamespace(value="active"),
        remaining_input=10,
        remaining_output=10,
        initial_input_tokens=100,
        initial_output_tokens=100,
        granted_input_tokens=100,
        granted_output_tokens=100,
        used_input_tokens=90,
        used_output_tokens=90,
        prefer_system_override=False,
    )


def _build_client(monkeypatch, *, cloud=True, enabled=True, has_budget=True, has_row=True):
    monkeypatch.setattr(mod, "_is_cloud_mode", lambda: cloud)
    # is_free_tier_active constructs UserProviderService(db) but never queries it,
    # so any db object works — hand back a bare mock.
    monkeypatch.setattr(mod, "get_db_client", AsyncMock(return_value=MagicMock()))

    app = FastAPI()
    app.include_router(mod.router)

    sys_svc = MagicMock()
    sys_svc.is_enabled.return_value = enabled
    sys_svc.get_config.return_value = SimpleNamespace(
        slots={"agent": SimpleNamespace(model="sys-agent-x")}
    )
    quota_svc = MagicMock()
    quota_svc.get = AsyncMock(return_value=(_fake_quota_row() if has_row else None))
    quota_svc.check = AsyncMock(return_value=has_budget)
    app.state.system_provider = sys_svc
    app.state.quota_service = quota_svc

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = "u1"
        return await call_next(request)

    return TestClient(app)


@pytest.mark.asyncio
async def test_quota_me_free_tier_active_locks_to_system_model(monkeypatch):
    client = _build_client(monkeypatch, enabled=True, has_budget=True, has_row=True)
    r = client.get("/api/quota/me")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["free_tier"] == {"active": True, "model": "sys-agent-x"}


@pytest.mark.asyncio
async def test_quota_me_free_tier_inactive_when_exhausted(monkeypatch):
    """Budget spent → own provider takes over → not locked, banner hidden."""
    client = _build_client(monkeypatch, enabled=True, has_budget=False, has_row=True)
    r = client.get("/api/quota/me")
    assert r.status_code == 200
    assert r.json()["free_tier"] == {"active": False, "model": None}


@pytest.mark.asyncio
async def test_quota_me_free_tier_present_when_uninitialized(monkeypatch):
    """No quota row yet → uninitialized, and free_tier is inactive (no budget)."""
    client = _build_client(monkeypatch, enabled=True, has_budget=True, has_row=False)
    r = client.get("/api/quota/me")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "uninitialized"
    assert body["free_tier"] == {"active": False, "model": None}


@pytest.mark.asyncio
async def test_quota_me_local_mode_has_no_free_tier(monkeypatch):
    """Local mode → feature strictly off, plain {enabled: false}, no lock block."""
    client = _build_client(monkeypatch, cloud=False)
    r = client.get("/api/quota/me")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}
