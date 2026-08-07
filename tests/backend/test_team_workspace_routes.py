"""
@file_name: test_team_workspace_routes.py
@author: NarraNexus
@date: 2026-08-07
@description: The team workspace's read surface, and its cleanup.

Two routes give the team room the panel it never had (`teams.py` carried no
artifact or file route at all), plus the cleanup that has to keep up with the
two new tables.

The cleanup half is the easier one to get wrong: `_wipe_team_data` deletes the
shared directory from disk, so if the index is not deleted with it the UI
happily lists files that no longer exist — rows pointing at nothing, which is
worse than showing nothing, because the user cannot tell the difference until
a download fails.
"""

from __future__ import annotations

import pytest

from backend.routes.teams import _wipe_team_data
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact
from xyz_agent_context.schema.team_schema import Team
from xyz_agent_context.utils.workspace_paths import team_shared_dir

OWNER = "user_t"
TID = "team_abc"
OTHER_TID = "team_other"


async def _seed_artifact(db, artifact_id, *, team_id, agent_id="agent_a"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    await ArtifactRepository(db).create(Artifact(
        artifact_id=artifact_id, agent_id=agent_id, user_id=OWNER,
        session_id=None, title=artifact_id, kind="text/markdown", pinned=True,
        team_id=team_id, file_path=f"p/{artifact_id}.md", size_bytes=1,
        created_at=now, updated_at=now,
    ))


async def _seed_file(db, file_id, *, team_id):
    await db.insert("team_files", {
        "file_id": file_id, "team_id": team_id, "owner_user_id": OWNER,
        "shared_by_agent_id": "agent_a", "original_name": f"{file_id}.md",
        "rel_path": f"p/{file_id}.md", "size_bytes": 1, "content_hash": file_id,
    })


# ── cleanup keeps the index and the disk in step ──────────────────────────


@pytest.mark.asyncio
async def test_clearing_files_also_clears_the_index(db_client, monkeypatch, tmp_path):
    """Rows that outlive their files are worse than no rows: the panel lists
    them, and the user only finds out when a download fails."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)
    d = team_shared_dir(OWNER, TID, str(tmp_path))
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text("x")

    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "f2", team_id=TID)
    await _seed_file(db_client, "keep", team_id=OTHER_TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    res = await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    assert res["files_removed"] is True
    left = await db_client.execute("SELECT * FROM team_files", fetch=True)
    assert {r["file_id"] for r in left} == {"keep"}, "another team's index must survive"


@pytest.mark.asyncio
async def test_clearing_files_also_clears_team_artifacts_and_their_history(
    db_client, monkeypatch, tmp_path
):
    """A team artifact points into the shared tree that just got deleted, so
    it has to go with it — and its attribution rows with it, or the history
    table accumulates orphans nothing will ever read."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_artifact(db_client, "art_other_team", team_id=OTHER_TID)
    await _seed_artifact(db_client, "art_private", team_id=None)
    for aid in ("art_team", "art_other_team", "art_private"):
        await db_client.insert("instance_artifact_history", {
            "artifact_id": aid, "agent_id": "agent_a", "file_path": "p", "action": "created",
        })

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_other_team", "art_private"}, (
        "only THIS team's artifacts go; private work and other teams are untouched"
    )
    hist = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifact_history", fetch=True)}
    assert "art_team" not in hist, "attribution rows must not outlive their artifact"


@pytest.mark.asyncio
async def test_clearing_only_chat_leaves_the_workspace_alone(db_client, monkeypatch, tmp_path):
    """The two scopes are independent; wiping chat must not touch output."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)
    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_artifact(db_client, "art_team", team_id=TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=True, clear_files=False)

    assert await db_client.execute("SELECT * FROM team_files", fetch=True)
    assert await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)


# ── the read surface ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_team_files_returns_only_that_team(db_client):
    from backend.routes.teams import _team_files

    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "nope", team_id=OTHER_TID)

    rows = await _team_files(db_client, TID)
    assert [r["file_id"] for r in rows] == ["f1"]


@pytest.mark.asyncio
async def test_team_files_are_newest_first(db_client):
    from backend.routes.teams import _team_files

    await _seed_file(db_client, "older", team_id=TID)
    await _seed_file(db_client, "newer", team_id=TID)

    rows = await _team_files(db_client, TID)
    assert [r["file_id"] for r in rows] == ["newer", "older"]
