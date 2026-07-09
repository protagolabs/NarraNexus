"""
@file_name: test_agents_llm_config_routes.py
@author: rujing.yan
@date: 2026-07-09
@description: Tests for the per-agent LLM config routes:
  GET    /api/agents/{agent_id}/llm-config
  PUT    /api/agents/{agent_id}/llm-config/{slot_name}
  DELETE /api/agents/{agent_id}/llm-config/{slot_name}

Regression guard for the owner-default assembly bug: the GET handler must read
the RAW ``user_slots`` rows (which carry ``params_json`` + ``agent_framework``),
NOT ``UserProviderService.get_user_config().slots`` (SlotConfig objects that drop
both). Feeding SlotConfig.model_dump() made every owner-default framework read as
claude_code and every reasoning param read as auto — which broke inheritance for
codex_cli owners (the panel then wrote claude_code into the override → 400). Plus
ownership (403) + override view coverage.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate

import backend.routes.agents_llm_config as mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _build_client(db_client, viewer_id: str = "u1", role: str = "user"):
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/agents")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        request.state.role = role
        return await call_next(request)

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db_factory as db_factory_mod
    db_factory_mod.get_db_client = _get_db_override
    mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed_agent(db_client, agent_id="ag1", owner="u1"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


async def _seed_user_agent_slot(
    db_client, user_id="u1", framework="codex_cli",
    provider_id="p_owner", model="gpt-5.4", thinking="on", reasoning="high",
):
    await db_client.insert(
        "user_slots",
        {
            "user_id": user_id,
            "slot_name": "agent",
            "provider_id": provider_id,
            "model": model,
            "agent_framework": framework,
            "params_json": json.dumps({"thinking": thinking, "reasoning_effort": reasoning}),
        },
    )


@pytest.mark.asyncio
async def test_get_returns_owner_default_codex_framework(db_client):
    """The blocker: an owner whose default framework is codex_cli must see
    codex_cli in owner_default + effective (was always claude_code)."""
    await _seed_agent(db_client)
    await _seed_user_agent_slot(db_client, framework="codex_cli", thinking="on", reasoning="high")
    client = _build_client(db_client, viewer_id="u1")

    r = client.get("/api/agents/ag1/llm-config")
    assert r.status_code == 200
    agent = r.json()["data"]["slots"]["agent"]
    assert agent["inheriting"] is True
    assert agent["owner_default"]["agent_framework"] == "codex_cli"
    assert agent["effective"]["agent_framework"] == "codex_cli"
    # Reasoning params from params_json must survive too (were forced to auto).
    assert agent["owner_default"]["thinking"] == "on"
    assert agent["owner_default"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_get_rejects_non_owner(db_client):
    await _seed_agent(db_client, owner="u1")
    client = _build_client(db_client, viewer_id="u2")  # not the owner
    r = client.get("/api/agents/ag1/llm-config")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_unknown_agent_404(db_client):
    client = _build_client(db_client, viewer_id="u1")
    r = client.get("/api/agents/ghost/llm-config")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_shows_override_when_present(db_client):
    await _seed_agent(db_client)
    await _seed_user_agent_slot(db_client, framework="claude_code")
    # A per-agent override that rebinds the agent slot to codex_cli.
    await db_client.insert(
        "agent_slots",
        {
            "agent_id": "ag1",
            "slot_name": "agent",
            "provider_id": "p_override",
            "model": "gpt-5.5",
            "agent_framework": "codex_cli",
            "params_json": json.dumps({"thinking": "", "reasoning_effort": ""}),
        },
    )
    client = _build_client(db_client, viewer_id="u1")

    r = client.get("/api/agents/ag1/llm-config")
    assert r.status_code == 200
    agent = r.json()["data"]["slots"]["agent"]
    assert agent["inheriting"] is False
    assert agent["override"]["agent_framework"] == "codex_cli"
    assert agent["effective"]["model"] == "gpt-5.5"
    # owner default is still surfaced (claude_code) for the "reset" affordance.
    assert agent["owner_default"]["agent_framework"] == "claude_code"


@pytest.mark.asyncio
async def test_reset_slot_clears_override(db_client):
    await _seed_agent(db_client)
    await db_client.insert(
        "agent_slots",
        {"agent_id": "ag1", "slot_name": "agent", "provider_id": "p", "model": "m",
         "agent_framework": "codex_cli"},
    )
    client = _build_client(db_client, viewer_id="u1")
    r = client.delete("/api/agents/ag1/llm-config/agent")
    assert r.status_code == 200
    rows = await db_client.get("agent_slots", {"agent_id": "ag1"})
    assert rows == []
