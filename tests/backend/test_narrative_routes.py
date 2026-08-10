"""
@file_name: test_narrative_routes.py
@author:
@date: 2026-08-10
@description: Route-level tests for the narrative MCP data-access-seam
endpoints (view_narrative / switch_narrative / create_narrative).

Covers: the canonical ownership gate (non-owner 403 shape, unknown-agent
404 shape — same pattern as test_channel_routes_owner_gate.py /
test_awareness_create_missing.py), the happy paths for all three
endpoints, and the underlying-failure (200 + success:false) shape.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.routes.agents import narrative as nr


class _FakeNarrativeRow(dict):
    pass


class _FakeDb:
    """Minimal AsyncDatabaseClient stand-in: get_one / get / get_by_ids are
    used by this route file (no raw SQL)."""

    def __init__(self, narratives=None, links=None, chat_memory=None):
        self._narratives = narratives or {}
        self._links = links or {}
        self._chat_memory = chat_memory or {}

    async def get_one(self, table, filters):
        if table == "narratives":
            return self._narratives.get(filters.get("narrative_id"))
        if table == "instance_json_format_memory_chat":
            return self._chat_memory.get(filters.get("instance_id"))
        raise AssertionError(f"unexpected get_one table: {table}")

    async def get(self, table, filters, limit=None, offset=None, order_by=None):
        if table == "instance_narrative_links":
            rows = self._links.get(filters.get("narrative_id"), [])
            return rows[:limit] if limit else rows
        raise AssertionError(f"unexpected get table: {table}")

    async def get_by_ids(self, table, id_field, ids):
        # Single-query batch fetch (the N+1 fix uses this instead of a
        # per-instance get_one loop).
        if table == "instance_json_format_memory_chat":
            return [self._chat_memory[i] for i in ids if i in self._chat_memory]
        raise AssertionError(f"unexpected get_by_ids table: {table}")


class _FakeNarrative:
    def __init__(self, id_):
        self.id = id_


@pytest.fixture
def fake_db():
    return _FakeDb(
        narratives={
            "nar_mine": {
                "narrative_id": "nar_mine",
                "agent_id": "agent_mine",
                "narrative_info": {
                    "name": "Trip planning",
                    "description": "Planning a trip",
                    "current_summary": "Discussing destinations",
                },
                "topic_keywords": ["travel", "trip"],
            },
            "nar_other_agent": {
                "narrative_id": "nar_other_agent",
                "agent_id": "agent_theirs",
                "narrative_info": {"name": "Not yours"},
                "topic_keywords": [],
            },
        },
        links={
            "nar_mine": [{"instance_id": "chat_abc123"}, {"instance_id": "aware_ignored"}],
        },
        chat_memory={
            "chat_abc123": {
                "memory": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "Where should I go?",
                            "meta_data": {"timestamp": "2026-08-10T10:00:00", "event_id": "evt_1"},
                        },
                        {
                            "role": "assistant",
                            "content": "How about Kyoto?",
                            "meta_data": {"timestamp": "2026-08-10T10:00:05", "event_id": "evt_2"},
                        },
                    ]
                }
            }
        },
    )


@pytest.fixture
def client(monkeypatch, fake_db):
    async def _db():
        return fake_db

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(nr, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(nr.router, prefix="/api/agents")
    c = TestClient(app)
    return c


OWNER_HEADERS = {"x-test-user": "u1"}


# ---------------------------------------------------------------------------
# Ownership gate — one representative chain per HTTP verb, per
# test_channel_routes_owner_gate.py's precedent (the other endpoints share
# the same assert_owned wiring).
# ---------------------------------------------------------------------------


def test_view_narrative_non_owner_is_denied(client):
    r = client.get("/api/agents/agent_theirs/narratives/nar_other_agent", headers=OWNER_HEADERS)
    assert r.status_code == 403


def test_view_narrative_unknown_agent_is_404(client):
    r = client.get("/api/agents/agent_ghost/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 404


def test_switch_narrative_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/narratives/nar_other_agent/switch", headers=OWNER_HEADERS)
    assert r.status_code == 403


def test_switch_narrative_unknown_agent_is_404(client):
    r = client.post("/api/agents/agent_ghost/narratives/nar_mine/switch", headers=OWNER_HEADERS)
    assert r.status_code == 404


def test_create_narrative_non_owner_is_denied(client):
    r = client.post(
        "/api/agents/agent_theirs/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic"},
    )
    assert r.status_code == 403


def test_create_narrative_unknown_agent_is_404(client):
    r = client.post(
        "/api/agents/agent_ghost/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# view_narrative
# ---------------------------------------------------------------------------


def test_view_narrative_returns_full_thread(client):
    r = client.get("/api/agents/agent_mine/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narrative_id"] == "nar_mine"
    assert body["name"] == "Trip planning"
    assert body["summary"] == "Discussing destinations"
    assert body["keywords"] == ["travel", "trip"]
    assert body["message_count"] == 2
    # only the chat_ instance's messages are pulled in, sorted by time
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == "Where should I go?"
    assert body["messages"][0]["event_id"] == "evt_1"


def test_view_narrative_not_found(client):
    r = client.get("/api/agents/agent_mine/narratives/nar_missing", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


def test_view_narrative_belonging_to_a_different_agent_reports_not_found(client):
    # agent_mine is a real, owned agent, but nar_other_agent belongs to
    # agent_theirs — cross-agent narrative access must not leak.
    r = client.get("/api/agents/agent_mine/narratives/nar_other_agent", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


def test_view_narrative_db_error_reports_success_false(client, monkeypatch):
    async def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(nr, "get_db_client", _boom)
    r = client.get("/api/agents/agent_mine/narratives/nar_mine", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "db unavailable" in body["error"]


# ---------------------------------------------------------------------------
# switch_narrative
# ---------------------------------------------------------------------------


def test_switch_narrative_success(client):
    r = client.post("/api/agents/agent_mine/narratives/nar_mine/switch", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narrative_id"] == "nar_mine"


def test_switch_narrative_not_found(client):
    r = client.post("/api/agents/agent_mine/narratives/nar_missing/switch", headers=OWNER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "not found" in body["error"]


# ---------------------------------------------------------------------------
# create_narrative
# ---------------------------------------------------------------------------


def test_create_narrative_success(client, monkeypatch):
    captured = {}

    async def fake_create(self, agent_id, user_id, title, description=""):
        captured["args"] = (agent_id, user_id, title, description)
        return _FakeNarrative("nar_new123")

    monkeypatch.setattr(nr.NarrativeService, "create_narrative", fake_create)

    r = client.post(
        "/api/agents/agent_mine/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic", "description": "About something new"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["narrative_id"] == "nar_new123"
    assert body["title"] == "New topic"
    assert captured["args"] == ("agent_mine", "u1", "New topic", "About something new")


def test_create_narrative_requires_title(client):
    r = client.post(
        "/api/agents/agent_mine/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "   "},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "title" in body["error"]


def test_create_narrative_underlying_failure_is_success_false(client, monkeypatch):
    async def fake_create(self, agent_id, user_id, title, description=""):
        raise RuntimeError("boom")

    monkeypatch.setattr(nr.NarrativeService, "create_narrative", fake_create)

    r = client.post(
        "/api/agents/agent_mine/narratives",
        headers=OWNER_HEADERS,
        json={"user_id": "u1", "title": "New topic"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "boom" in body["error"]
