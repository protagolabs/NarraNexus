"""
@file_name: conftest.py
@description: Shared fixtures for dashboard v2 backend tests.

Strategy: we query the real configured DB (MySQL or SQLite) and clean up by
agent_id prefix at fixture setup. Each fixture seeds unique rows; concurrent
tests must not share agent_id namespaces.
"""
from __future__ import annotations

import asyncio
import uuid
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def tmp_empty_db():
    """Yield the global AsyncDatabaseClient (shared across tests). Ensures schema migrated."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate

    db = await get_db_client()
    try:
        await auto_migrate(db._backend)
    except Exception:
        pass  # may already be migrated
    yield db


async def _clean_by_prefix(db, table: str, field: str, prefix: str):
    """Delete rows where `field` starts with `prefix`. Works cross-backend."""
    try:
        await db.execute(
            f"DELETE FROM {table} WHERE {field} LIKE %s",
            (f"{prefix}%",),
            fetch=False,
        )
    except Exception:
        pass


@pytest_asyncio.fixture
async def tmp_db_with_events(tmp_empty_db):
    db = tmp_empty_db
    from datetime import datetime, timedelta, timezone

    suffix = uuid.uuid4().hex[:8]
    agent_a = f"agent_a_{suffix}"
    agent_b = f"agent_b_{suffix}"
    agent_c = f"agent_c_{suffix}"

    now = datetime.now(timezone.utc)
    t_2h_ago = (now - timedelta(hours=2)).isoformat()
    t_1h_ago = (now - timedelta(hours=1)).isoformat()
    t_now = now.isoformat()

    await _clean_by_prefix(db, "events", "agent_id", f"agent_a_{suffix}")
    await _clean_by_prefix(db, "events", "agent_id", f"agent_b_{suffix}")

    await db.insert("events", {"event_id": f"e1_{suffix}", "agent_id": agent_a,
        "trigger": "CHAT", "trigger_source": "web",
        "created_at": t_2h_ago, "updated_at": t_2h_ago})
    await db.insert("events", {"event_id": f"e2_{suffix}", "agent_id": agent_a,
        "trigger": "CHAT", "trigger_source": "web",
        "created_at": t_now, "updated_at": t_now})
    await db.insert("events", {"event_id": f"e3_{suffix}", "agent_id": agent_b,
        "trigger": "JOB", "trigger_source": "scheduler",
        "created_at": t_1h_ago, "updated_at": t_1h_ago})

    yield {
        "db": db,
        "agent_a": agent_a,
        "agent_b": agent_b,
        "agent_c": agent_c,
        "latest_ts_a": t_now,
        "latest_ts_b": t_1h_ago,
    }


@pytest_asyncio.fixture
async def tmp_db_with_jobs(tmp_empty_db):
    db = tmp_empty_db
    suffix = uuid.uuid4().hex[:8]
    agent_a = f"agent_j_{suffix}"
    ts = "2026-04-13T00:00:00"
    await _clean_by_prefix(db, "instance_jobs", "agent_id", f"agent_j_{suffix}")

    for idx, (jid, title, status) in enumerate([
        ("j1", "running1", "running"),
        ("j2", "pending1", "pending"),
        ("j3", "active1", "active"),
    ]):
        await db.insert("instance_jobs", {
            "instance_id": f"inst{idx}_{suffix}",
            "job_id": f"{jid}_{suffix}",
            "agent_id": agent_a, "user_id": "alice", "title": title,
            "description": f"desc for {title}",
            "job_type": "report", "status": status,
            "created_at": ts, "updated_at": ts,
        })
    yield {"db": db, "agent_a": agent_a}


@pytest_asyncio.fixture
async def tmp_seeded_db(tmp_empty_db):
    """Seed 2 users + 4 agents for permission tests with unique suffix per test."""
    db = tmp_empty_db
    suffix = uuid.uuid4().hex[:8]
    alice = f"alice_{suffix}"
    bob = f"bob_{suffix}"
    for uid, dn in [(alice, "Alice"), (bob, "Bob")]:
        try:
            await db.insert("users", {
                "user_id": uid, "user_type": "local",
                "role": "user", "display_name": dn,
            })
        except Exception:
            pass

    agents = [
        (f"a1_{suffix}", alice, 0),
        (f"a2_{suffix}", alice, 1),
        (f"b1_{suffix}", bob, 1),
        (f"b2_{suffix}", bob, 0),
    ]
    for aid, owner, pub in agents:
        try:
            await db.insert("agents", {
                "agent_id": aid, "agent_name": aid,
                "agent_description": None,
                "created_by": owner, "is_public": pub,
            })
        except Exception:
            pass

    yield {
        "db": db,
        "alice": alice, "bob": bob,
        "a1": f"a1_{suffix}", "a2": f"a2_{suffix}",
        "b1": f"b1_{suffix}", "b2": f"b2_{suffix}",
    }


@pytest.fixture
def local_client_seeded(tmp_seeded_db, monkeypatch):
    """TestClient with local-mode auth → viewer=alice (unique per test)."""
    from fastapi.testclient import TestClient

    alice = tmp_seeded_db["alice"]

    async def _fake_local_user():
        return alice

    monkeypatch.setattr("backend.auth.get_local_user_id", _fake_local_user)
    try:
        monkeypatch.setattr("backend.routes.dashboard.get_local_user_id", _fake_local_user)
    except (AttributeError, ImportError):
        pass

    from backend.main import app

    yield {
        "client": TestClient(app),
        "ctx": tmp_seeded_db,
    }
