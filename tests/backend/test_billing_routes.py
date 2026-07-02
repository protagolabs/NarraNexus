"""
@file_name: test_billing_routes.py
@author: NarraNexus
@date: 2026-07-02
@description: Route tests for backend/routes/billing.py (NetMind billing proxy).

Mirrors tests/backend/test_provider_oauth_gating.py: a FastAPI TestClient with a
fake auth middleware, cloud mode forced via env, and the billing client stubbed
so no real network happens. Verifies cloud gating, token requirement, and
error mapping (auth -> 401, upstream -> 502).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.billing as billing_mod
from xyz_agent_context.services.netmind_billing_client import (
    BillingAuthError,
    BillingUpstreamError,
)

USER = {"X-User-Id": "user_test"}
_ME_FREE = {"plan_id": "free", "subscription": None}


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient with fake auth middleware + forced deployment mode."""

    def _make(*, cloud: bool):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud" if cloud else "local")
        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request: Request, call_next):
            request.state.user_id = request.headers.get("X-User-Id") or None
            return await call_next(request)

        app.include_router(billing_mod.router, prefix="/api/billing")
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _stub_client(monkeypatch, *, plans=None, me=None, raise_exc=None):
    class _Stub:
        async def get_plans(self):
            if raise_exc:
                raise raise_exc
            return plans if plans is not None else {"plans": []}

        async def get_subscription(self, token):
            if raise_exc:
                raise raise_exc
            return me if me is not None else _ME_FREE

    monkeypatch.setattr(billing_mod, "_client", lambda: _Stub())


# --- cloud gating -----------------------------------------------------------

def test_plans_404_in_local_mode(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=False)
    assert client.get("/api/billing/plans").status_code == 404


def test_subscription_404_in_local_mode(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=False)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 404


# --- plans (public) ---------------------------------------------------------

def test_plans_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, plans={"plans": [{"plan_id": "pro"}]})
    client = make_client(cloud=True)
    r = client.get("/api/billing/plans")
    assert r.status_code == 200
    assert r.json()["data"]["plans"][0]["plan_id"] == "pro"


def test_plans_upstream_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingUpstreamError("down"))
    client = make_client(cloud=True)
    assert client.get("/api/billing/plans").status_code == 502


# --- subscription (loginToken) ---------------------------------------------

def test_subscription_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, me={"plan_id": "pro", "subscription": {"status": "ACTIVE"}})
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 200
    assert r.json()["data"]["subscription"]["status"] == "ACTIVE"


def test_subscription_missing_netmind_token_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    # local identity present, but no X-Netmind-Token
    r = client.get("/api/billing/subscription", headers=USER)
    assert r.status_code == 401


def test_subscription_bad_token_maps_to_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 401


def test_subscription_upstream_maps_to_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingUpstreamError("down"))
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 502


def test_subscription_unauthenticated_local_identity_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    # No X-User-Id -> resolve_current_user_id raises 401 before token check
    r = client.get("/api/billing/subscription", headers={"X-Netmind-Token": "jwt"})
    assert r.status_code == 401
