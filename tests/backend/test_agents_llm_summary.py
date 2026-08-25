"""
@file_name: test_agents_llm_summary.py
@author: NarraNexus
@date: 2026-08-24
@description: Effective framework/model enrichment for GET /api/auth/agents.

The Agents directory renders each agent's actual runtime choice. The listing
must resolve user defaults and per-agent overrides in bulk; callers must not
issue one llm-config request per table row.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = "alice"
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_listing_resolves_owner_default_and_agent_override(client, db_client):
    for agent_id in ("agent_inherited", "agent_override"):
        _run(db_client.insert("agents", {
            "agent_id": agent_id,
            "agent_name": agent_id,
            "created_by": "alice",
        }))

    _run(db_client.insert("user_slots", {
        "user_id": "alice",
        "slot_name": "agent",
        "provider_id": "owner-provider",
        "model": "claude-sonnet-4-5",
        "agent_framework": "claude_code",
    }))
    _run(db_client.insert("agent_slots", {
        "agent_id": "agent_override",
        "slot_name": "agent",
        "provider_id": "override-provider",
        "model": "gpt-5.5",
        "agent_framework": "codex_cli",
    }))

    response = client.get("/api/auth/agents")
    assert response.status_code == 200, response.text
    agents = {item["agent_id"]: item for item in response.json()["agents"]}

    assert agents["agent_inherited"]["agent_framework"] == "claude_code"
    assert agents["agent_inherited"]["model"] == "claude-sonnet-4-5"
    assert agents["agent_override"]["agent_framework"] == "codex_cli"
    assert agents["agent_override"]["model"] == "gpt-5.5"
