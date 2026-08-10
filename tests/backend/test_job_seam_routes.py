"""
@file_name: test_job_seam_routes.py
@author:
@date: 2026-08-10
@description: Route-level tests for the job MCP data-access-seam endpoints
(GET /{agent_id}/jobs/{job_id}, POST .../jobs/search-semantic, .../search-keywords).
Owner-gated byte-parity twins of the JobModule read tools. Mirrors the
test_narrative_routes.py fixture shape.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import backend.routes.agents.jobs as jr
import xyz_agent_context.module.job_module._job_reads as jreads


class _V:
    def __init__(self, v):
        self.value = v


class _FakeJob:
    def __init__(self, job_id, agent_id):
        self.job_id = job_id
        self.agent_id = agent_id
        self.user_id = "u1"
        self.instance_id = "job_inst"
        self.title = "T"
        self.description = "D"
        self.payload = {}
        self.job_type = _V("one_off")
        self.trigger_config = None
        self.status = _V("active")
        self.notification_method = "none"
        self.next_run_at_local = None
        self.next_run_tz = "UTC"
        self.last_run_at_local = None
        self.last_run_tz = "UTC"
        self.related_entity_id = None
        self.narrative_id = None
        self.iteration_count = 0
        self.process = "proc"
        self.last_error = None
        self.created_at = None
        self.updated_at = None


class _FakeJobRepo:
    def __init__(self, db):
        pass

    async def get_job(self, job_id):
        # job_mine owned by agent_mine; job_theirs owned by agent_theirs
        if job_id == "job_mine":
            return _FakeJob("job_mine", "agent_mine")
        if job_id == "job_theirs":
            return _FakeJob("job_theirs", "agent_theirs")
        return None

    async def search_keyword(self, agent_id, query, user_id, status, limit):
        return [(_FakeJob("job_mine", agent_id), 0.9)]

    async def search_by_keywords(self, agent_id, keywords, user_id, status, limit):
        return [_FakeJob("job_mine", agent_id)]


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(jr, "get_db_client", _db)
    monkeypatch.setattr(jreads, "JobRepository", _FakeJobRepo)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(jr.router, prefix="/api/agents")
    return TestClient(app)


OWNER = {"x-test-user": "u1"}


def test_job_by_id_non_owner_is_denied(client):
    r = client.get("/api/agents/agent_theirs/jobs/job_theirs", headers=OWNER)
    assert r.status_code == 403


def test_job_search_semantic_non_owner_is_denied(client):
    r = client.post("/api/agents/agent_theirs/jobs/search-semantic", headers=OWNER, json={"query": "x"})
    assert r.status_code == 403


def test_job_by_id_returns_detail(client):
    r = client.get("/api/agents/agent_mine/jobs/job_mine", headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["job"]["job_id"] == "job_mine"
    assert body["job"]["process"] == "proc"


def test_job_by_id_cross_agent_is_access_denied(client):
    # job_theirs is owned by agent_theirs; agent_mine (the caller's own agent)
    # must not read it.
    r = client.get("/api/agents/agent_mine/jobs/job_theirs", headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "Access denied" in body["error"]


def test_job_by_id_not_found(client):
    r = client.get("/api/agents/agent_mine/jobs/job_ghost", headers=OWNER)
    assert r.status_code == 200
    assert r.json() == {"success": False, "error": "Job not found: job_ghost"}


def test_job_search_semantic_returns_results(client):
    r = client.post("/api/agents/agent_mine/jobs/search-semantic", headers=OWNER, json={"query": "news"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total_results"] == 1
    assert body["jobs"][0]["similarity_score"] == 0.9


def test_job_search_keywords_returns_results(client):
    r = client.post("/api/agents/agent_mine/jobs/search-keywords", headers=OWNER, json={"keywords": ["news"]})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["total_results"] == 1


def test_job_search_keywords_requires_keywords(client):
    r = client.post("/api/agents/agent_mine/jobs/search-keywords", headers=OWNER, json={"keywords": []})
    assert r.status_code == 422
