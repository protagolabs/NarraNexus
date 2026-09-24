"""
@file_name: test_policy_management.py
@date: 2026-09-23
@description: Owner-managed script permissions and retired website-rule API.
"""
import asyncio
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routes import browser as routes
from narranexus.platform.browser._browser_impl.approval_store import ApprovalStore
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, decide
from narranexus.platform.browser._browser_impl.policy_store import PolicyStore
from narranexus.platform.browser.browser_service import BrowserService


@pytest.mark.asyncio
async def test_policy_view_exposes_only_script_permissions(db_client):
    store = PolicyStore(db_client)
    assert await store.get_public("agent") == {
        "agent_id": "agent", "defaults": {"full_cdp_access": "deny"}, "origins": [],
    }
    await store.set_rule("agent", origin="https://site.example", capability="full_cdp_access", verdict="allow")
    view = await store.get_public("agent")
    assert view["origins"] == [{"origin": "https://site.example", "full_cdp_access": "allow"}]
    await store.set_rule("agent", origin="https://site.example", capability="full_cdp_access", verdict="deny")
    assert (await store.get_public("agent"))["origins"][0]["full_cdp_access"] == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,capability,verdict", [
    ("file:///private", "full_cdp_access", "allow"),
    ("https://x.example/path", "full_cdp_access", "allow"),
    ("https://user:password@x.example", "full_cdp_access", "allow"),
    ("https://x.example?token=secret", "full_cdp_access", "allow"),
    ("https://x.example", "full_cdp_access", "ask"),
    ("https://x.example", "uploads", "allow"),
    ("https://x.example", "access", "allow"),
    ("https://x.example", "access", "deny"),
])
async def test_invalid_management_requests_never_change_policy(db_client, origin, capability, verdict):
    with pytest.raises(ValueError):
        await PolicyStore(db_client).set_rule("agent", origin=origin, capability=capability, verdict=verdict)
    assert await db_client.get("instance_browser_policies", {"agent_id": "agent"}) == []


@pytest.mark.asyncio
async def test_concurrent_script_updates_preserve_independent_approval_answers(db_client):
    approvals = ApprovalStore(db_client)
    pending = await approvals.request(agent_id="agent", origin="https://files.example", capability="uploads",
                                      turn_id="one", thread_id="thread")
    results = await asyncio.gather(
        PolicyStore(db_client).set_rule("agent", origin="https://one.example", capability="full_cdp_access", verdict="allow"),
        PolicyStore(db_client).set_rule("agent", origin="https://two.example", capability="full_cdp_access", verdict="deny"),
        approvals.resolve(pending["approval_id"], agent_id="agent", decision="allow", lifetime="always"),
    )
    assert results[-1]
    raw = await db_client.get_one("instance_browser_policies", {"agent_id": "agent"})
    policy = BrowserPolicy.from_dict(json.loads(raw["policy_json"]))
    assert decide(policy, url="https://one.example", capability="full_cdp_access").verdict == "allow"
    assert decide(policy, url="https://two.example", capability="full_cdp_access").verdict == "deny"
    assert decide(policy, url="https://files.example", capability="uploads").verdict == "allow"


@pytest.fixture
def client(monkeypatch, tmp_path):
    service = BrowserService(locate=lambda: None)
    monkeypatch.setattr(routes, "get_service", lambda: service)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.user_id = request.headers.get("X-User-Id")
        return await call_next(request)

    async def owned(request, agent_id):
        if request.state.user_id != "owner" or agent_id != "agent":
            raise HTTPException(403)

    monkeypatch.setattr(routes, "assert_owned", owned)
    app.include_router(routes.router, prefix="/api/browser")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/browser/policy/agent", None),
    ("PUT", "/api/browser/policy/agent", {"origin": "https://site.example", "capability": "full_cdp_access", "verdict": "allow"}),
    ("POST", "/api/browser/policy/agent/revoke", {"origin": "https://site.example", "capability": "full_cdp_access"}),
])
def test_management_routes_require_identity_and_agent_owner(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 401
    assert client.request(method, path, json=body, headers={"X-User-Id": "other"}).status_code == 403


def test_owner_can_view_allow_and_revoke_with_strict_payload(client):
    headers = {"X-User-Id": "owner"}
    path = "/api/browser/policy/agent"
    assert client.get(path, headers=headers).status_code == 200
    body = {"origin": "https://SITE.example:443/", "capability": "full_cdp_access", "verdict": "allow"}
    response = client.put(path, json=body, headers=headers)
    assert response.status_code == 200
    assert next(row for row in response.json()["origins"] if row["origin"] == "https://site.example")["full_cdp_access"] == "allow"
    response = client.post(path + "/revoke", json={"origin": "https://site.example", "capability": "full_cdp_access"}, headers=headers)
    assert response.status_code == 200
    assert next(row for row in response.json()["origins"] if row["origin"] == "https://site.example")["full_cdp_access"] == "deny"
    assert client.put(path, json={**body, "capability": "uploads"}, headers=headers).status_code == 422
    assert client.put(path, json={**body, "grants": []}, headers=headers).status_code == 422


def test_storage_corruption_returns_retryable_failure_not_invalid_user_input(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError("corrupt stored policy")

    monkeypatch.setattr(routes.get_service(), "set_policy_rule", fail)
    response = client.put("/api/browser/policy/agent", headers={"X-User-Id": "owner"}, json={
        "origin": "https://site.example", "capability": "full_cdp_access", "verdict": "allow",
    })
    assert response.status_code == 503


@pytest.mark.parametrize("verdict", ["allow", "ask", "deny"])
def test_owner_cannot_recreate_website_access_rules(client, verdict):
    headers = {"X-User-Id": "owner"}
    body = {"origin": "https://site.example", "capability": "access"}
    before = client.get("/api/browser/policy/agent", headers=headers).json()
    assert client.put("/api/browser/policy/agent", json={**body, "verdict": verdict}, headers=headers).status_code == 422
    assert client.post("/api/browser/policy/agent/revoke", json=body, headers=headers).status_code == 422
    assert client.get("/api/browser/policy/agent", headers=headers).json() == before
