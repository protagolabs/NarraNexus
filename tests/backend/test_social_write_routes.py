"""
@file_name: test_social_write_routes.py
@author:
@date: 2026-08-10
@description: Route-level proof for the SocialNetworkModule write endpoints
(extract / merge / delete-entity / create-agent).

Follows the `test_channel_routes_owner_gate.py` fixture shape: one TestClient
per test, monkeypatching `backend.routes._ownership` for the ownership
decision and the route module's own collaborators (InstanceRepository,
SocialNetworkRepository, SocialNetworkModule, AgentRepository,
InstanceAwarenessRepository) for the data operations —
these routes replicate `_social_mcp_tools.py`'s tool bodies, not the DB
itself, so the tests pin behavior at that boundary.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import backend.routes.agents.social_network as sn_routes
from backend.routes.agents.social_network import router as social_network_router


# --------------------------------------------------------------------------- fakes


class _FakeInstance:
    def __init__(self, instance_id: str):
        self.instance_id = instance_id


class _FakeInstanceRepository:
    """Stands in for InstanceRepository — resolves a SocialNetworkModule
    instance for every agent except 'agent_no_instance'."""

    def __init__(self, db):
        self.db = db

    async def get_by_agent(self, agent_id: str, module_class: str):
        if agent_id == "agent_no_instance":
            return []
        return [_FakeInstance(f"social_{agent_id}")]

    async def create_instance(self, instance):
        return 1


class _FakeSocialEntity:
    def __init__(
        self,
        entity_id: str,
        entity_name: str = "",
        keywords: list[str] | None = None,
        identity_info: dict | None = None,
        contact_info: dict | None = None,
        related_job_ids: list[str] | None = None,
        entity_description: str = "",
        interaction_count: int = 0,
        last_interaction_time=None,
    ):
        self.entity_id = entity_id
        self.entity_name = entity_name
        self.keywords = keywords or []
        self.identity_info = identity_info or {}
        self.contact_info = contact_info or {}
        self.related_job_ids = related_job_ids or []
        self.entity_description = entity_description
        self.interaction_count = interaction_count
        self.last_interaction_time = last_interaction_time


class _FakeSocialNetworkRepository:
    """Stands in for SocialNetworkRepository. `known` maps entity_id ->
    _FakeSocialEntity; missing ids resolve to None (not found)."""

    def __init__(self, db, known: dict[str, _FakeSocialEntity]):
        self.db = db
        self.known = known
        self.updated: tuple[str, dict] | None = None
        self.deleted: list[str] = []

    async def get_entity(self, entity_id: str, instance_id: str):
        return self.known.get(entity_id)

    async def update_entity_info(self, entity_id: str, instance_id: str, updates: dict):
        self.updated = (entity_id, updates)

    async def delete_entity(self, entity_id: str, instance_id: str):
        self.deleted.append(entity_id)


class _FakeSocialNetworkModule:
    """Stands in for SocialNetworkModule — records the call and returns a
    canned extract_and_update_entity_info result."""

    last_call: dict[str, Any] | None = None
    result: dict[str, Any] = {"success": True, "message": "Entity info updated successfully", "entity_id": "x"}

    def __init__(self, agent_id, database_client, instance_id):
        self.agent_id = agent_id
        self.instance_id = instance_id

    async def extract_and_update_entity_info(self, entity_id, instance_id, updates, update_mode):
        type(self).last_call = {
            "entity_id": entity_id,
            "instance_id": instance_id,
            "updates": updates,
            "update_mode": update_mode,
        }
        return type(self).result


class _FakeAgent:
    def __init__(self, agent_id: str, created_by: str | None, agent_name: str = "Creator"):
        self.agent_id = agent_id
        self.created_by = created_by
        self.agent_name = agent_name


class _FakeAgentRepository:
    def __init__(self, db, agents: dict[str, _FakeAgent]):
        self.db = db
        self.agents = agents
        self.added: dict | None = None

    async def get_agent(self, agent_id: str):
        return self.agents.get(agent_id)

    async def add_agent(self, **kwargs):
        self.added = kwargs


class _FakeInstanceAwarenessRepository:
    def __init__(self, db):
        self.db = db
        self.upserted: tuple[str, str] | None = None

    async def upsert(self, instance_id: str, awareness: str):
        self.upserted = (instance_id, awareness)
        return True


# --------------------------------------------------------------------------- fixture


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(sn_routes, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2", "agent_no_instance": "u1"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    monkeypatch.setattr(sn_routes, "InstanceRepository", _FakeInstanceRepository)
    # Never touch the real filesystem base path for create-agent.

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(social_network_router, prefix="/api/agents")
    return TestClient(app)


OWNER_HEADERS = {"x-test-user": "u1"}


# --------------------------------------------------------------------------- ownership gate


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/agents/agent_theirs/social-network/extract", {"entity_id": "e1", "updates": {}}),
        ("/api/agents/agent_theirs/social-network/merge", {"source_entity_id": "a", "target_entity_id": "b"}),
        ("/api/agents/agent_theirs/social-network/delete-entity", {"entity_id": "e1"}),
        (
            "/api/agents/agent_theirs/social-network/create-agent",
            {"agent_name": "Scout", "awareness": "I am Scout"},
        ),
    ],
)
def test_non_owner_write_is_denied_by_the_canonical_helper(client, path, body):
    r = client.post(path, headers=OWNER_HEADERS, json=body)
    assert r.status_code == 403
    assert "Permission denied" in r.json()["detail"]


# --------------------------------------------------------------------------- extract


def test_extract_success_delegates_to_social_network_module(client, monkeypatch):
    _FakeSocialNetworkModule.result = {
        "success": True,
        "message": "Entity info updated successfully",
        "entity_id": "user_alice",
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/extract",
        headers=OWNER_HEADERS,
        json={"entity_id": "user_alice", "updates": {"entity_name": "Alice"}, "update_mode": "merge"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["entity_id"] == "user_alice"
    assert _FakeSocialNetworkModule.last_call == {
        "entity_id": "user_alice",
        "instance_id": "social_agent_mine",
        "updates": {"entity_name": "Alice"},
        "update_mode": "merge",
    }


def test_extract_underlying_failure_maps_message_to_error_key(client, monkeypatch):
    _FakeSocialNetworkModule.result = {
        "success": False,
        "message": "Error: updates must be a dictionary, got list",
        "entity_id": "user_bob",
    }
    monkeypatch.setattr(sn_routes, "SocialNetworkModule", _FakeSocialNetworkModule)

    r = client.post(
        "/api/agents/agent_mine/social-network/extract",
        headers=OWNER_HEADERS,
        json={"entity_id": "user_bob", "updates": {}},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Error: updates must be a dictionary, got list"
    assert "message" not in body


def test_extract_no_social_network_instance_reports_family_style_error(client):
    r = client.post(
        "/api/agents/agent_no_instance/social-network/extract",
        headers=OWNER_HEADERS,
        json={"entity_id": "user_x", "updates": {}},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "No SocialNetworkModule instance found for agent: agent_no_instance"


# --------------------------------------------------------------------------- merge


def test_merge_success_unions_tags_and_deletes_source(client, monkeypatch):
    source = _FakeSocialEntity(
        "entity_alice_lark",
        entity_name="Alice (Lark)",
        keywords=["expert:ml", "engineer"],
        identity_info={"organization": "Acme"},
        entity_description="Met via Lark",
        interaction_count=3,
    )
    target = _FakeSocialEntity(
        "user_alice_123",
        entity_name="Alice",
        keywords=["expert:ml"],
        identity_info={"position": "Lead"},
        entity_description="",
        interaction_count=5,
    )
    fake_repo = _FakeSocialNetworkRepository(None, {"entity_alice_lark": source, "user_alice_123": target})
    monkeypatch.setattr(sn_routes, "SocialNetworkRepository", lambda db: fake_repo)

    r = client.post(
        "/api/agents/agent_mine/social-network/merge",
        headers=OWNER_HEADERS,
        json={"source_entity_id": "entity_alice_lark", "target_entity_id": "user_alice_123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["target_entity_id"] == "user_alice_123"
    assert set(body["merged_tags"]) == {"expert:ml", "engineer"}

    updated_entity_id, updates = fake_repo.updated
    assert updated_entity_id == "user_alice_123"
    assert updates["identity_info"] == {"position": "Lead", "organization": "Acme"}
    assert updates["interaction_count"] == 8
    assert fake_repo.deleted == ["entity_alice_lark"]


def test_merge_missing_source_entity_returns_family_style_error(client, monkeypatch):
    target = _FakeSocialEntity("user_alice_123", entity_name="Alice")
    fake_repo = _FakeSocialNetworkRepository(None, {"user_alice_123": target})
    monkeypatch.setattr(sn_routes, "SocialNetworkRepository", lambda db: fake_repo)

    r = client.post(
        "/api/agents/agent_mine/social-network/merge",
        headers=OWNER_HEADERS,
        json={"source_entity_id": "ghost", "target_entity_id": "user_alice_123"},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Source entity not found: ghost"
    assert fake_repo.deleted == []


# --------------------------------------------------------------------------- delete-entity


def test_delete_entity_success(client, monkeypatch):
    entity = _FakeSocialEntity("entity_junk", entity_name="Junk Entry")
    fake_repo = _FakeSocialNetworkRepository(None, {"entity_junk": entity})
    monkeypatch.setattr(sn_routes, "SocialNetworkRepository", lambda db: fake_repo)

    r = client.post(
        "/api/agents/agent_mine/social-network/delete-entity",
        headers=OWNER_HEADERS,
        json={"entity_id": "entity_junk"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "permanently deleted" in body["message"]
    assert fake_repo.deleted == ["entity_junk"]


def test_delete_missing_entity_returns_family_style_error(client, monkeypatch):
    fake_repo = _FakeSocialNetworkRepository(None, {})
    monkeypatch.setattr(sn_routes, "SocialNetworkRepository", lambda db: fake_repo)

    r = client.post(
        "/api/agents/agent_mine/social-network/delete-entity",
        headers=OWNER_HEADERS,
        json={"entity_id": "ghost"},
    )
    body = r.json()
    assert body["success"] is False
    assert body["error"] == "Entity not found: ghost"
    assert fake_repo.deleted == []


# --------------------------------------------------------------------------- create-agent


def test_create_agent_success(client, monkeypatch):
    caller = _FakeAgent("agent_mine", created_by="u1", agent_name="Owner Agent")
    fake_agent_repo = _FakeAgentRepository(None, {"agent_mine": caller})
    fake_awareness_repo = _FakeInstanceAwarenessRepository(None)
    monkeypatch.setattr(sn_routes, "AgentRepository", lambda db: fake_agent_repo)
    monkeypatch.setattr(sn_routes, "InstanceAwarenessRepository", lambda db: fake_awareness_repo)

    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"agent_name": "Scout", "awareness": "I am Scout, a research helper."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["agent_name"] == "Scout"
    assert body["new_agent_id"].startswith("agent_")

    assert fake_agent_repo.added["created_by"] == "u1"
    assert fake_agent_repo.added["agent_name"] == "Scout"
    assert fake_awareness_repo.upserted[1] == "I am Scout, a research helper."


def test_create_agent_without_resolvable_owner_returns_family_style_error(client, monkeypatch):
    caller = _FakeAgent("agent_mine", created_by=None)
    fake_agent_repo = _FakeAgentRepository(None, {"agent_mine": caller})
    monkeypatch.setattr(sn_routes, "AgentRepository", lambda db: fake_agent_repo)

    r = client.post(
        "/api/agents/agent_mine/social-network/create-agent",
        headers=OWNER_HEADERS,
        json={"agent_name": "Scout", "awareness": "I am Scout."},
    )
    body = r.json()
    assert body["success"] is False
    assert "Cannot determine your owner" in body["error"]
