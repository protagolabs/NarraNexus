"""
@file_name: test_agents_channel_summary.py
@author: NarraNexus
@date: 2026-08-24
@description: Bound-channel projection for GET /api/auth/agents.

The Agents directory needs one compact channel summary per row. The listing
must batch that projection and must not disclose public agents' bindings to a
different viewer.
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


def test_listing_batches_owned_channel_bindings_without_leaking_public_agents(client, db_client):
    _run(db_client.insert("agents", {
        "agent_id": "agent_owned",
        "agent_name": "Owned",
        "created_by": "alice",
    }))
    _run(db_client.insert("agents", {
        "agent_id": "agent_public",
        "agent_name": "Public",
        "created_by": "bob",
        "is_public": 1,
    }))

    for agent_id, profile in (("agent_owned", "owned-profile"), ("agent_public", "public-profile")):
        _run(db_client.insert("lark_credentials", {
            "agent_id": agent_id,
            "app_id": f"app-{agent_id}",
            "app_secret_ref": f"secret-{agent_id}",
            "brand": "lark",
            "profile_name": profile,
            "auth_status": "authorized",
            "is_active": 1,
        }))

    _run(db_client.insert("channel_telegram_credentials", {
        "agent_id": "agent_owned",
        "bot_token_encoded": "encoded-token",
        "enabled": 0,
    }))

    response = client.get("/api/auth/agents")
    assert response.status_code == 200, response.text
    agents = {item["agent_id"]: item for item in response.json()["agents"]}

    # Bound means a credential exists; an inactive binding still appears.
    assert agents["agent_owned"]["bound_channels"] == ["lark", "telegram"]
    # Public directory rows never expose another owner's integration metadata.
    assert agents["agent_public"]["bound_channels"] == []
