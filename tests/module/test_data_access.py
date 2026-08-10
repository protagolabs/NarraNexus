"""
@file_name: test_data_access.py
@author:
@date: 2026-08-10
@description: AgentDataStore seam — REAL parity between DirectStore and
HttpStore, the backend 200+success:false failure contract, the identity-header
forwarding whitelist, and the composition root's env gate.

Parity here means: the same scenario produces the same RETURN STRING from both
implementations. The fake backend below implements the actual route semantics
(backend/routes/agents/awareness.py with create_missing=false): failure is
HTTP 200 + {"success": false, "error": ...}; non-2xx only ever comes from the
transport/middleware layer (e.g. the Q6 identity 401).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from xyz_agent_context.module.data_access import (
    DirectStore,
    HttpStore,
    get_agent_data_store,
)
from xyz_agent_context.module.data_access import store as st
from xyz_agent_context.module.data_access.factory import current_identity_headers

AGENT = "agent_39b2b72b823b"


# ---------------------------------------------------------------------------
# A fake backend implementing the REAL route contract, and a fake db world
# giving DirectStore the same known agents — parity = same scenario, same string.
# ---------------------------------------------------------------------------


def _patch_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=transport, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


def _http_store(monkeypatch, known_agents, upsert_ok=True, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="rejected")
        agent_id = request.url.path.split("/")[3]
        assert request.url.params.get("create_missing") == "false"
        if agent_id not in known_agents:
            return httpx.Response(200, json={
                "success": False,
                "error": f"No AwarenessModule instance found for agent_id={agent_id}",
            })
        if not upsert_ok:
            return httpx.Response(200, json={
                "success": False, "error": "Failed to update awareness",
            })
        return httpx.Response(200, json={"success": True, "awareness": "x"})

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def _direct_store(known_agents):
    store = DirectStore()

    async def fake_db():
        return object()

    async def fake_instance_id(db, agent_id):
        return f"aware_{agent_id}" if agent_id in known_agents else None

    store._db = fake_db  # type: ignore[method-assign]
    store._awareness_instance_id = fake_instance_id  # type: ignore[method-assign]
    return store


@pytest.fixture
def _upsert_spy(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeAwarenessRepo:
        def __init__(self, db):
            pass

        async def upsert(self, instance_id, awareness):
            calls.append((instance_id, awareness))
            return True

    monkeypatch.setattr(
        "xyz_agent_context.repository.InstanceAwarenessRepository", FakeAwarenessRepo
    )
    return calls


# ---------------------------------------------------------------------------
# parity: same scenario, same string
# ---------------------------------------------------------------------------


def test_parity_success(monkeypatch, _upsert_spy):
    direct = _direct_store({AGENT})
    http = _http_store(monkeypatch, {AGENT})

    d = asyncio.run(direct.update_awareness(AGENT, "hello"))
    h = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert d == h == st._AWARENESS_OK
    assert _upsert_spy == [(f"aware_{AGENT}", "hello")]


def test_parity_unknown_agent_is_an_error_on_both(monkeypatch, _upsert_spy):
    """The direct path treats an unknown agent_id as an ERROR — the Http path
    must too, NOT auto-create an instance for it (pre-review C2: the route's
    convenience auto-create is opted out via create_missing=false)."""
    direct = _direct_store(set())
    http = _http_store(monkeypatch, set())

    d = asyncio.run(direct.update_awareness(AGENT, "hello"))
    h = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert d == h == st._no_instance_msg(AGENT)
    assert _upsert_spy == []


def test_http_backend_reported_failure_is_not_success(monkeypatch):
    """Pre-review C1: the route reports failure as HTTP 200 + success:false —
    a status-code-only check would call this 'updated successfully'."""
    http = _http_store(monkeypatch, {AGENT}, upsert_ok=False)
    out = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert out == "Error: Failed to update awareness"


def test_http_transport_rejection_degrades_in_band(monkeypatch):
    """Pre-review I3: a middleware-layer 401/5xx (e.g. the Q6 identity gate
    before keys are provisioned) must come back as a readable string, never
    escape the MCP tool as an exception."""
    http = _http_store(monkeypatch, {AGENT}, status=401)
    out = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert out == "Error: awareness backend rejected the call (401)"


def test_http_unreachable_backend_degrades_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route to backend")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.update_awareness(AGENT, "hello"))
    assert out == "Error: awareness backend unreachable (ConnectError)"


def test_direct_resolves_instance_through_repository(monkeypatch, _upsert_spy):
    """The one piece of real logic DirectStore carries — instance resolution —
    exercised against the repository seam instead of being stubbed away
    (pre-review I1)."""

    class _Inst:
        instance_id = "aware_real"

    class FakeInstanceRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            assert module_class == "AwarenessModule"
            return [_Inst()] if agent_id == AGENT else []

    monkeypatch.setattr(
        "xyz_agent_context.repository.InstanceRepository", FakeInstanceRepo
    )

    async def fake_db():
        return object()

    store = DirectStore()
    store._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(store.update_awareness(AGENT, "hi")) == st._AWARENESS_OK
    assert asyncio.run(
        store.update_awareness("agent_nobody", "hi")
    ) == st._no_instance_msg("agent_nobody")


# ---------------------------------------------------------------------------
# identity-header forwarding whitelist
# ---------------------------------------------------------------------------


def test_identity_header_whitelist(monkeypatch):
    ambient = {
        "x-narranexus-agent-id": AGENT,
        "x-narranexus-team-id": "team_1",
        "authorization": "Bearer nx-agent:agent_a~chat",
        "cookie": "session=SECRET",
        "x-forwarded-for": "1.2.3.4",
        "content-type": "application/json",
    }
    monkeypatch.setattr(
        "xyz_agent_context.module._mcp_identity._ambient_headers", lambda: ambient
    )
    kept = current_identity_headers()
    assert kept == {
        "x-narranexus-agent-id": AGENT,
        "x-narranexus-team-id": "team_1",
        "authorization": "Bearer nx-agent:agent_a~chat",
    }


def test_identity_headers_empty_without_ambient_request(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.module._mcp_identity._ambient_headers", lambda: None
    )
    assert current_identity_headers() == {}


# ---------------------------------------------------------------------------
# composition root
# ---------------------------------------------------------------------------


def test_factory_default_is_direct(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)
    assert isinstance(get_agent_data_store(), DirectStore)


def test_factory_cloud_is_http(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    store = get_agent_data_store(identity_headers={"authorization": "Bearer nx-agent:a"})
    assert isinstance(store, HttpStore)
    assert store._base == "http://backend:8000"
    assert store._headers == {"authorization": "Bearer nx-agent:a"}


def test_http_store_sends_identity_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    store = HttpStore(
        "http://backend:8000",
        identity_headers={"Authorization": "Bearer nx-agent:a~chat"},
    )
    asyncio.run(store.update_awareness(AGENT, "x"))
    assert seen["auth"] == "Bearer nx-agent:a~chat"
