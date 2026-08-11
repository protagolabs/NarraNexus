"""
@file_name: test_narramessenger_prewarm.py
@date: 2026-08-11
@description: Prewarm endpoints — bearer auth, agent resolution, broker seam.
"""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

import backend.routes.channels.narramessenger as nm


class _FakeCred:
    agent_id = "agent_x"
    bearer_token = "secret-tok"
    enabled = True


class _FakeDisabledCred(_FakeCred):
    enabled = False


@pytest.fixture
def app(monkeypatch):
    async def fake_get_db():
        return object()
    monkeypatch.setattr(nm, "_get_db", fake_get_db)

    async def fake_by_mxid(self, mxid):
        return _FakeCred() if mxid == "@agent-x:hs" else None
    monkeypatch.setattr(
        nm.NarramessengerCredentialManager, "get_by_matrix_user_id", fake_by_mxid
    )

    async def fake_owner(self, agent_id):
        return "user_1"
    monkeypatch.setattr(nm.AgentRepository, "resolve_owner", fake_owner)

    monkeypatch.setattr(nm, "broker_url", lambda: "http://broker:8030")
    ensured = []

    async def fake_ensure(user_id, **kw):
        ensured.append(user_id)
        return nm.ExecutorEnsureResult(url="http://nx-exec-user-1:8020", cold_started=True)
    monkeypatch.setattr(nm, "ensure_executor", fake_ensure)

    async def fake_wait(url, **kw):
        return None
    monkeypatch.setattr(nm, "wait_until_ready", fake_wait)
    nm._PREWARM_STATE.clear()

    a = FastAPI()
    a.include_router(nm.router, prefix="/api/narramessenger")
    a.state.ensured = ensured
    yield a
    nm._PREWARM_STATE.clear()
    nm._PREWARM_TASKS.clear()


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


BODY = {"agent_matrix_user_id": "@agent-x:hs", "rtc_session_id": "rtc-1"}
AUTH = {"Authorization": "Bearer secret-tok"}


@pytest.mark.asyncio
async def test_prewarm_happy_path_202_and_ensures_owner(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["status"] == "warming"
    await asyncio.sleep(0)
    assert app.state.ensured == ["user_1"]


@pytest.mark.asyncio
async def test_prewarm_missing_bearer_401(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY)
    assert r.status_code == 401
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_wrong_bearer_403_and_no_ensure(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY,
                         headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_unknown_agent_404(app):
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm",
                         json={"agent_matrix_user_id": "@ghost:hs"}, headers=AUTH)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_prewarm_disabled_binding_404_and_no_ensure(app, monkeypatch):
    async def fake_disabled(self, mxid):
        return _FakeDisabledCred() if mxid == "@agent-x:hs" else None
    monkeypatch.setattr(
        nm.NarramessengerCredentialManager, "get_by_matrix_user_id", fake_disabled
    )
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 404
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_profile_id_path_202_and_ensures_owner(app, monkeypatch):
    async def fake_by_profile(self, profile_id):
        return _FakeCred() if profile_id == "prof-x" else None
    monkeypatch.setattr(
        nm.NarramessengerCredentialManager, "get_by_profile_id", fake_by_profile
    )
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm",
                         json={"agent_profile_id": "prof-x"}, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["status"] == "warming"
    await asyncio.sleep(0)
    assert app.state.ensured == ["user_1"]


@pytest.mark.asyncio
async def test_prewarm_identifier_misuse_422(app):
    async with _client(app) as c:
        r0 = await c.post("/api/narramessenger/prewarm", json={}, headers=AUTH)
        r2 = await c.post("/api/narramessenger/prewarm",
                          json={"agent_matrix_user_id": "@agent-x:hs",
                                "agent_profile_id": "p1"}, headers=AUTH)
    assert r0.status_code == 422
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_prewarm_idempotent_second_call_already_warm(app, monkeypatch):
    async def alive(url, **kw):
        return True
    monkeypatch.setattr(nm, "_executor_alive", alive)
    async with _client(app) as c:
        await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)
        r2 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r2.status_code == 202
    assert r2.json()["status"] == "already_warm"


@pytest.mark.asyncio
async def test_prewarm_no_broker_skipped(app, monkeypatch):
    monkeypatch.setattr(nm, "broker_url", lambda: None)
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["status"] == "skipped"
    assert app.state.ensured == []


@pytest.mark.asyncio
async def test_prewarm_ensure_failure_records_failed_and_retries(app, monkeypatch):
    async def broken_ensure(user_id, **kw):
        raise RuntimeError("broker down")
    monkeypatch.setattr(nm, "ensure_executor", broken_ensure)
    async with _client(app) as c:
        r1 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        assert r1.status_code == 202
        assert r1.json()["status"] == "warming"
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert nm._PREWARM_STATE["user_1"]["status"] == "failed"
        rs = await c.get("/api/narramessenger/prewarm/status",
                         params={"agent_matrix_user_id": "@agent-x:hs"}, headers=AUTH)
        assert rs.status_code == 200
        assert rs.json() == {"ready": False}

        async def working_ensure(user_id, **kw):
            app.state.ensured.append(user_id)
            return nm.ExecutorEnsureResult(
                url="http://nx-exec-user-1:8020", cold_started=True
            )
        monkeypatch.setattr(nm, "ensure_executor", working_ensure)
        r2 = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)
    assert r2.status_code == 202
    assert r2.json()["status"] == "warming"  # a failed state must not wedge retries
    assert app.state.ensured == ["user_1"]


@pytest.mark.asyncio
async def test_prewarm_owner_lookup_failure_503(app, monkeypatch):
    async def owner_none(self, agent_id):
        return None
    monkeypatch.setattr(nm.AgentRepository, "resolve_owner", owner_none)
    async with _client(app) as c:
        r = await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_status_reports_ready_from_state(app, monkeypatch):
    async def alive(url, **kw):
        return True
    monkeypatch.setattr(nm, "_executor_alive", alive)
    async with _client(app) as c:
        await c.post("/api/narramessenger/prewarm", json=BODY, headers=AUTH)
        await asyncio.sleep(0)
        r = await c.get("/api/narramessenger/prewarm/status",
                        params={"agent_matrix_user_id": "@agent-x:hs"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ready": True}


@pytest.mark.asyncio
async def test_status_unknown_state_not_ready(app):
    async with _client(app) as c:
        r = await c.get("/api/narramessenger/prewarm/status",
                        params={"agent_matrix_user_id": "@agent-x:hs"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ready": False}


def test_prewarm_paths_are_auth_exempt():
    from backend.auth import AUTH_EXEMPT_PATHS
    assert "/api/narramessenger/prewarm" in AUTH_EXEMPT_PATHS
    assert "/api/narramessenger/prewarm/status" in AUTH_EXEMPT_PATHS
