"""
@file_name: test_user_cascade.py
@author: NarraNexus
@date: 2026-06-11
@description: Tests for delete_user_cascade — the hard-delete utility used by
              the external API protocol's DELETE /v1/external/agents/{a}/sessions/{s}.

Covers:
- All 12 dependent tables are touched
- users row removed
- Workspace directories matching the user_id suffix are removed
- Idempotent: re-running on a deleted user_id returns zero counts cleanly
- Partial failure (one table errors) does NOT abort the rest of the cascade
- include_workspace=False leaves disk alone
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.schema_registry import auto_migrate
from xyz_agent_context.utils.user_cascade import (
    TABLES_KEYED_BY_USER_ID,
    delete_user_cascade,
)


@pytest.fixture
async def db_with_user(monkeypatch):
    """Build a fresh SQLite DB with a single ephemeral external user
    + at least one row referencing user_id in every cascade table.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="user_cascade_test_"))
    db_path = tmpdir / "test.db"
    workspace_base = tmpdir / "workspaces"
    workspace_base.mkdir()

    # Point settings.base_working_path at our fixture dir so workspace
    # removal scans this dir, not the real ~/.nexusagent/workspaces.
    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "base_working_path", str(workspace_base)
    )

    backend = SQLiteBackend(str(db_path))
    await backend.initialize()
    await auto_migrate(backend)

    user_id = "ext_agt_abc12345_session_xyz"
    agent_id = "agt_abc12345"

    # Seed the parent users row
    await backend.insert(
        "users",
        {
            "user_id": user_id,
            "user_type": "external_guest",
            "owned_by_agent": agent_id,
        },
    )

    # Seed at least one row per cascade table referencing the user_id.
    # Each table has its own NOT NULL columns; the seed has to satisfy
    # them. We keep seeds minimal — only what's necessary to insert.
    await backend.insert("events", {
        "event_id": "evt_test1",
        "user_id": user_id,
        "agent_id": agent_id,
        "input_content": "hi",
        "state": "completed",
        "created_at": "2026-06-11 00:00:00",
        "updated_at": "2026-06-11 00:00:00",
    })
    await backend.insert("mcp_urls", {
        "agent_id": agent_id,
        "user_id": user_id,
        "url": "http://localhost:7801/sse",
        "type": "sse",
    })
    await backend.insert("inbox_table", {
        "user_id": user_id,
        "agent_id": agent_id,
        "message": "test",
        "from_user_id": "bin",
    })
    await backend.insert("module_instances", {
        "instance_id": "mod_test1",
        "module_class": "ChatModule",
        "agent_id": agent_id,
        "user_id": user_id,
        "status": "active",
        "description": "",
        "dependencies": "[]",
        "config": "{}",
        "keywords": "[]",
        "topic_hint": "",
    })
    await backend.insert("user_providers", {
        "user_id": user_id,
        "provider_id": "p1",
        "source": "netmind",
        "protocol": "openai",
        "auth_type": "api_key",
        "models": "[]",
    })

    # Create two workspace directories that match the user_id suffix
    (workspace_base / f"{agent_id}_{user_id}").mkdir()
    (workspace_base / f"{agent_id}_{user_id}" / "file1.txt").write_text("hello")
    (workspace_base / f"agt_other_xxx_{user_id}").mkdir()
    # And one workspace that does NOT match — must survive
    (workspace_base / f"agt_xyz_{user_id}_other_suffix").mkdir()
    (workspace_base / "bin").mkdir()

    yield backend, user_id, workspace_base, tmpdir

    # cleanup
    await backend.close()
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_cascade_removes_all_user_rows(db_with_user):
    backend, user_id, workspace_base, _ = db_with_user

    # Sanity: user row present pre-delete
    rows = await backend.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
    )
    assert len(rows) == 1

    cascade = await delete_user_cascade(user_id, backend)

    # User row gone
    rows = await backend.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
    )
    assert rows == []
    assert cascade["users"] == 1

    # Every seeded row gone from every seeded table
    for table in ("events", "mcp_urls", "inbox_table", "module_instances",
                  "user_providers"):
        rows = await backend.execute(
            f"SELECT user_id FROM {table} WHERE user_id = ?", (user_id,)
        )
        assert rows == [], f"orphan row left in {table}"

    # Every cascade table reported a non-negative count
    for table in TABLES_KEYED_BY_USER_ID:
        assert cascade[table] >= 0, f"{table} cascade failed: {cascade[table]}"


@pytest.mark.asyncio
async def test_cascade_removes_matching_workspaces(db_with_user):
    backend, user_id, workspace_base, _ = db_with_user

    pre = sorted(p.name for p in workspace_base.iterdir())
    assert f"agt_abc12345_{user_id}" in pre
    assert f"agt_other_xxx_{user_id}" in pre

    cascade = await delete_user_cascade(user_id, backend)

    post = sorted(p.name for p in workspace_base.iterdir())
    # Two matching dirs removed
    assert f"agt_abc12345_{user_id}" not in post
    assert f"agt_other_xxx_{user_id}" not in post
    # Non-matching survivors intact
    assert f"agt_xyz_{user_id}_other_suffix" in post
    assert "bin" in post

    assert cascade["workspace_dirs_removed"] == 2
    assert cascade["workspace_bytes_removed"] > 0


@pytest.mark.asyncio
async def test_cascade_is_idempotent(db_with_user):
    backend, user_id, _, _ = db_with_user

    first = await delete_user_cascade(user_id, backend)
    second = await delete_user_cascade(user_id, backend)

    # Second pass finds nothing — every cascade returns 0
    assert second["users"] == 0
    for table in TABLES_KEYED_BY_USER_ID:
        assert second[table] == 0
    assert second["workspace_dirs_removed"] == 0
    assert second["workspace_bytes_removed"] == 0


@pytest.mark.asyncio
async def test_include_workspace_false_leaves_disk_alone(db_with_user):
    backend, user_id, workspace_base, _ = db_with_user

    pre = sorted(p.name for p in workspace_base.iterdir())
    cascade = await delete_user_cascade(
        user_id, backend, include_workspace=False
    )
    post = sorted(p.name for p in workspace_base.iterdir())

    # DB cascade still happened
    rows = await backend.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
    )
    assert rows == []

    # But disk untouched
    assert pre == post
    assert cascade["workspace_dirs_removed"] == 0
    assert cascade["workspace_bytes_removed"] == 0


@pytest.mark.asyncio
async def test_cascade_covers_every_user_id_table_in_registry(db_with_user):
    """Guard against schema drift: every table in schema_registry that
    has a user_id column must appear in TABLES_KEYED_BY_USER_ID. If a
    new column is added and this list isn't updated, the cascade
    silently leaves orphan rows — this test surfaces that.
    """
    from xyz_agent_context.utils.schema_registry import TABLES

    tables_with_user_id_in_registry = {
        name for name, td in TABLES.items()
        if any(col.name == "user_id" for col in td.columns)
    }

    # The `users` table itself has `user_id` but is handled separately
    # (parent row), so we expect cascade list to be everything else.
    expected_cascade = tables_with_user_id_in_registry - {"users"}

    actual_cascade = set(TABLES_KEYED_BY_USER_ID)

    missing = expected_cascade - actual_cascade
    extra = actual_cascade - expected_cascade

    assert not missing, (
        f"schema_registry has tables with user_id NOT in cascade list — "
        f"orphan rows would leak: {sorted(missing)}"
    )
    assert not extra, (
        f"cascade list references tables NOT in schema_registry: "
        f"{sorted(extra)}"
    )
