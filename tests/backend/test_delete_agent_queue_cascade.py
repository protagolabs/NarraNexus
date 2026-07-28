"""
@file_name: test_delete_agent_queue_cascade.py
@author: Bin Liang
@date: 2026-07-23
@description: DELETE /api/auth/agents/{agent_id} must cascade the
memory_consolidation_queue AND agent_cli_sessions.

Why (queue): agent deletion cleaned memory_* tables but left the agent's queue
rows behind. The consolidation worker's idle trigger then reprocessed
those scopes on every poll, each pass logging
"[background-llm] agent ... has no owner row" — the prod 1,880-warnings/14d
orphan-agent noise (bug tracker: "Agent 无 owner 记录").

Why (agent_cli_sessions, 2026-07-28): the resume table was added without a
cascade entry, so handles outlived both the agent and its workspace — a
recycled agent_id would inherit a handle pointing at a dead transcript, and
nothing ever pruned the rows.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.schema_registry import auto_migrate

import backend.routes.auth as auth_mod

_QUEUE = "memory_consolidation_queue"
_CLI_SESSIONS = "agent_cli_sessions"


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _restore_get_db():
    import xyz_agent_context.utils.db.db_factory as db_factory_mod
    original_factory = db_factory_mod.get_db_client
    original_auth = auth_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original_factory
    auth_mod.get_db_client = original_auth


def _build_client(db_client, viewer_id: str = "user_x"):
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = viewer_id
        return await call_next(request)

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db.db_factory as db_factory_mod

    db_factory_mod.get_db_client = _get_db_override
    auth_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed(db, agent_id="agent_a", owner="user_x"):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner})
    await db.insert(_QUEUE, {
        "agent_id": agent_id, "scope_type": "agent", "scope_id": "",
        "kind": "observation", "pending_count": 3, "status": "dirty",
    })
    await db.insert(_QUEUE, {
        "agent_id": agent_id, "scope_type": "narrative", "scope_id": "nar_1",
        "kind": "chat", "pending_count": 1, "status": "dirty",
    })
    # Another agent's queue row must survive.
    await db.insert("agents", {"agent_id": "agent_b", "agent_name": "B", "created_by": owner})
    await db.insert(_QUEUE, {
        "agent_id": "agent_b", "scope_type": "agent", "scope_id": "",
        "kind": "observation", "pending_count": 1, "status": "dirty",
    })


@pytest.mark.asyncio
async def test_delete_agent_removes_its_consolidation_queue_rows(db_client):
    await _seed(db_client)
    client = _build_client(db_client)

    resp = client.delete("/api/auth/agents/agent_a")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    rows = await db_client.execute(
        f"SELECT agent_id FROM {_QUEUE} ORDER BY agent_id", params=(), fetch=True,
    )
    assert [r["agent_id"] for r in rows] == ["agent_b"]


async def _seed_cli_sessions(db, agent_id: str, platform_session_id: str):
    await db.insert(_CLI_SESSIONS, {
        "agent_id": agent_id,
        "platform_session_id": platform_session_id,
        "narrative_id": "nar_1",
        "framework": "claude_code",
        "cli_session_id": f"cli_{agent_id}",
        "config_fingerprint": "fp0123456789abcd",
        "working_path": f"/ws/user_x/{agent_id}",
    })


@pytest.mark.asyncio
async def test_delete_agent_sweeps_its_cli_session_handles(db_client):
    await _seed(db_client)
    await _seed_cli_sessions(db_client, "agent_a", "sess_a1")
    await _seed_cli_sessions(db_client, "agent_a", "sess_a2")
    # Another agent's handle must survive.
    await _seed_cli_sessions(db_client, "agent_b", "sess_b1")
    client = _build_client(db_client)

    resp = client.delete("/api/auth/agents/agent_a")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    # The sweep is reported in deleted_counts, like every other cascaded table.
    assert body["deleted_counts"][_CLI_SESSIONS] == 2

    rows = await db_client.execute(
        f"SELECT agent_id FROM {_CLI_SESSIONS} ORDER BY agent_id",
        params=(), fetch=True,
    )
    assert [r["agent_id"] for r in rows] == ["agent_b"]


@pytest.mark.asyncio
async def test_delete_agent_without_cli_sessions_omits_the_stat(db_client):
    await _seed(db_client)
    client = _build_client(db_client)

    resp = client.delete("/api/auth/agents/agent_a")
    assert resp.status_code == 200
    # Zero-count tables are omitted from deleted_counts (existing convention).
    assert _CLI_SESSIONS not in resp.json()["deleted_counts"]
