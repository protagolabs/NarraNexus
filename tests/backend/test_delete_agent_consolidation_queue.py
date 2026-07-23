"""
@file_name: test_delete_agent_consolidation_queue.py
@author: NarraNexus
@date: 2026-07-23
@description: REGRESSION test — delete_agent must sweep the agent's rows out of
``memory_consolidation_queue``.

Why this file exists:
    delete_agent cleans ~20 per-agent tables (events, narratives, memory_*,
    agent_slots, …) but historically FORGOT ``memory_consolidation_queue`` —
    the table the background consolidation worker polls. Deleting an agent
    left its dirty queue rows behind as orphans; the worker kept picking them
    up, could not resolve the (now gone) owner, and spammed
    ``[background-llm] agent … has no owner row`` warnings for ~ever.

    This pins the fix (binding rule #8: sweep adjacent code) and guards the
    strict-scoping invariant: a delete of agent A must NOT touch agent B's
    queue rows.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.schema_registry import auto_migrate

import backend.routes.auth as auth_mod


_QUEUE = "memory_consolidation_queue"


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _patch_route_deps(db_client, monkeypatch):
    """delete_agent resolves the caller identity + db from module-level names."""
    async def _get_db():
        return db_client

    monkeypatch.setattr(auth_mod, "get_db_client", _get_db)
    monkeypatch.setattr(
        auth_mod, "resolve_current_user_id", AsyncMock(return_value="user_owner")
    )


def _queue_row(agent_id: str, *, kind: str = "observation", scope_id: str = ""):
    return {
        "agent_id": agent_id,
        "scope_type": "agent",
        "scope_id": scope_id,
        "kind": kind,
        "pending_count": 3,
        "status": "dirty",
    }


@pytest.mark.asyncio
async def test_delete_agent_purges_its_consolidation_queue_rows(db_client):
    # Arrange — an owned agent with two queued scopes.
    await db_client.insert(
        "agents",
        {"agent_id": "agent_a", "agent_name": "A", "created_by": "user_owner"},
    )
    await db_client.insert(_QUEUE, _queue_row("agent_a", kind="observation"))
    await db_client.insert(_QUEUE, _queue_row("agent_a", kind="entity"))

    # Act
    resp = await auth_mod.delete_agent("agent_a", request=object())  # type: ignore[arg-type]

    # Assert — route succeeded and every queue row for the agent is gone.
    assert resp.success is True
    assert await db_client.get(_QUEUE, {"agent_id": "agent_a"}) == []
    assert resp.deleted_counts.get(_QUEUE, 0) == 2


@pytest.mark.asyncio
async def test_delete_agent_leaves_other_agents_queue_rows(db_client):
    # Arrange — two agents, both owned by the same user; only agent_a is deleted.
    await db_client.insert(
        "agents",
        {"agent_id": "agent_a", "agent_name": "A", "created_by": "user_owner"},
    )
    await db_client.insert(
        "agents",
        {"agent_id": "agent_b", "agent_name": "B", "created_by": "user_owner"},
    )
    await db_client.insert(_QUEUE, _queue_row("agent_a"))
    await db_client.insert(_QUEUE, _queue_row("agent_b"))

    # Act
    resp = await auth_mod.delete_agent("agent_a", request=object())  # type: ignore[arg-type]

    # Assert — agent_a swept, agent_b untouched (strict per-agent scoping).
    assert resp.success is True
    assert await db_client.get(_QUEUE, {"agent_id": "agent_a"}) == []
    assert len(await db_client.get(_QUEUE, {"agent_id": "agent_b"})) == 1
