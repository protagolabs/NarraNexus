"""
@file_name: test_channel_routes_owner_gate.py
@author:
@date: 2026-08-10
@description: Route-level proof that a channel route actually consults the
canonical ownership helper (PR #258 review, round-1 minor #9 / round-3 #5).

test_ownership.py exercises the helper in isolation; the home_assistant test
breakage showed that "the route is really wired to it" is a separate fact CI
cannot see (it runs ruff only). One TestClient chain — POST /api/slack/bind as
a NON-owner — pins the wiring; the other five channel routes call the same
alias import, so one chain suffices.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.routes.channels.slack import router as slack_router


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(slack_router, prefix="/api/slack")
    return TestClient(app)


def test_non_owner_bind_is_denied_by_the_canonical_helper(client):
    r = client.post(
        "/api/slack/bind",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_theirs", "bot_token": "xoxb-0000000000", "app_token": "xapp-0000000000"},
    )
    assert r.status_code == 200  # channel routes wrap denials in a 200 payload
    body = r.json()
    assert body["success"] is False
    assert "Permission denied" in body["error"]


def test_unknown_agent_bind_reports_not_found(client):
    r = client.post(
        "/api/slack/bind",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_ghost", "bot_token": "xoxb-0000000000", "app_token": "xapp-0000000000"},
    )
    assert r.json()["success"] is False
    assert "not found" in r.json()["error"]


def test_db_failure_bubbles_as_503_not_200(client, monkeypatch):
    async def _boom(self, agent_id):
        return None  # resolve_owner's lookup-failed sentinel

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _boom)
    r = client.post(
        "/api/slack/bind",
        headers={"x-test-user": "u1"},
        json={"agent_id": "agent_mine", "bot_token": "xoxb-0000000000", "app_token": "xapp-0000000000"},
    )
    assert r.status_code == 503
