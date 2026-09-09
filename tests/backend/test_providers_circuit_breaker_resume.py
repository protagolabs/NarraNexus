"""
@file_name: test_providers_circuit_breaker_resume.py
@author:
@date: 2026-07-13
@description: providers route auto-resume wrapper delegates to the breaker and
never fails the reconfigure.
"""
from unittest.mock import MagicMock

import pytest

import backend.routes.providers as prov_routes
import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from backend.routes.providers import _resume_agent_circuit_breakers


def _mock_request(user_id: str = "u1") -> MagicMock:
    req = MagicMock()
    req.state.user_id = user_id
    req.state.role = "user"
    req.query_params = {}
    return req


@pytest.mark.asyncio
async def test_resume_delegates_to_reset_for_owner(monkeypatch):
    seen = []

    async def fake_reset(user_id, db=None):
        seen.append(user_id)
        return 1
    monkeypatch.setattr(cb, "reset_for_owner", fake_reset)

    await _resume_agent_circuit_breakers("alice")
    assert seen == ["alice"]


@pytest.mark.asyncio
async def test_resume_swallows_errors(monkeypatch):
    async def boom(user_id, db=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(cb, "reset_for_owner", boom)

    # Must NOT raise — provider reconfigure must never fail on breaker resume.
    await _resume_agent_circuit_breakers("bob")


# --------------------------------------------------------------------------
# GitHub #117: a provider status/test check confirming the credential works
# must resume PAUSED agents, not just leave that to a manual reset.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_test_success_resumes_breakers(monkeypatch):
    calls = []

    async def fake_resume(uid):
        calls.append(uid)
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)

    class FakeService:
        async def test_provider(self, uid, provider_id):
            return True, "ok"

    async def fake_get_service():
        return FakeService()
    monkeypatch.setattr(prov_routes, "_get_service", fake_get_service)

    resp = await prov_routes.test_provider("p1", _mock_request("alice"))
    assert resp["success"] is True
    assert calls == ["alice"]


@pytest.mark.asyncio
async def test_provider_test_failure_does_not_resume_breakers(monkeypatch):
    calls = []

    async def fake_resume(uid):
        calls.append(uid)
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)

    class FakeService:
        async def test_provider(self, uid, provider_id):
            return False, "dead key"

    async def fake_get_service():
        return FakeService()
    monkeypatch.setattr(prov_routes, "_get_service", fake_get_service)

    resp = await prov_routes.test_provider("p1", _mock_request("alice"))
    assert resp["success"] is False
    assert calls == []  # a still-broken provider must not resume anything


@pytest.mark.asyncio
async def test_claude_status_logged_in_resumes_breakers(monkeypatch):
    calls = []

    async def fake_resume(uid):
        calls.append(uid)
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)
    monkeypatch.setattr(prov_routes, "_is_cloud", lambda: False)

    async def fake_probe(args, timeout):
        return {"loggedIn": True, "email": "alice@example.com"}
    monkeypatch.setattr(prov_routes, "_run_json_subprocess", fake_probe)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("shutil.which", lambda x: "/usr/bin/claude" if x == "claude" else None)
        resp = await prov_routes.get_claude_status(_mock_request("alice"))

    assert resp["data"]["logged_in"] is True
    assert calls == ["alice"]


@pytest.mark.asyncio
async def test_claude_status_not_logged_in_does_not_resume(tmp_path, monkeypatch):
    calls = []

    async def fake_resume(uid):
        calls.append(uid)
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)
    monkeypatch.setattr(prov_routes, "_is_cloud", lambda: False)
    # Isolate from any real ~/.claude/.credentials.json on the host running
    # the test — the fallback path in get_claude_status reads Path.home()
    # via a function-local `from pathlib import Path`, so patch the class
    # itself (shared across every import of it).
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

    async def fake_probe(args, timeout):
        return None
    monkeypatch.setattr(prov_routes, "_run_json_subprocess", fake_probe)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("shutil.which", lambda x: None)
        resp = await prov_routes.get_claude_status(_mock_request("alice"))

    assert resp["data"]["logged_in"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_codex_status_logged_in_resumes_breakers(tmp_path, monkeypatch):
    calls = []

    async def fake_resume(uid):
        calls.append(uid)
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)
    monkeypatch.setattr(prov_routes, "_is_cloud", lambda: False)

    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("shutil.which", lambda x: "/usr/bin/codex" if x == "codex" else None)
        resp = await prov_routes.get_codex_status(_mock_request("alice"))

    assert resp["data"]["logged_in"] is True
    assert calls == ["alice"]


@pytest.mark.asyncio
async def test_codex_status_not_logged_in_does_not_resume(tmp_path, monkeypatch):
    calls = []

    async def fake_resume(uid):
        calls.append(uid)
    monkeypatch.setattr(prov_routes, "_resume_agent_circuit_breakers", fake_resume)
    monkeypatch.setattr(prov_routes, "_is_cloud", lambda: False)

    # Empty CODEX_HOME — no auth.json → not logged in.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("shutil.which", lambda x: "/usr/bin/codex" if x == "codex" else None)
        resp = await prov_routes.get_codex_status(_mock_request("alice"))

    assert resp["data"]["logged_in"] is False
    assert calls == []
