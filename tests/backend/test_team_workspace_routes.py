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
async def test_clearing_files_removes_only_this_teams_index(db_client, monkeypatch, tmp_path):
    """Scope is the team, never the owner: another team's index and any
    private work belong to neither this folder nor this switch."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "keep", team_id=OTHER_TID)
    await _seed_artifact(db_client, "art_private", team_id=None)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    files = {r["file_id"] for r in
             await db_client.execute("SELECT * FROM team_files", fetch=True)}
    assert files == {"keep"}
    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_private"}


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


# ── which turn produced which artifact ────────────────────────────────────
#
# The chip under a team message needs to know what THAT turn produced. The
# link is the events-row id, carried by both bus_messages and the artifact
# history — a real key, not a timestamp guess, which matters because the
# ordinary cases defeat guessing: one turn registering two artifacts, or two
# agents answering in the same room at once.


async def _seed_history(db, artifact_id, *, event_id, agent_id="agent_a"):
    await db.insert("instance_artifact_history", {
        "artifact_id": artifact_id, "agent_id": agent_id,
        "file_path": "p", "action": "created", "event_id": event_id,
    })


@pytest.mark.asyncio
async def test_turn_map_groups_artifacts_by_event(db_client):
    from backend.routes.teams import _team_artifact_turns

    await _seed_artifact(db_client, "art_1", team_id=TID)
    await _seed_artifact(db_client, "art_2", team_id=TID)
    await _seed_history(db_client, "art_1", event_id="evt_a")
    await _seed_history(db_client, "art_2", event_id="evt_a")

    assert await _team_artifact_turns(db_client, TID) == {"evt_a": ["art_1", "art_2"]}


@pytest.mark.asyncio
async def test_turn_map_excludes_other_teams(db_client):
    """A chip must never point at another team's work."""
    from backend.routes.teams import _team_artifact_turns

    await _seed_artifact(db_client, "art_mine", team_id=TID)
    await _seed_artifact(db_client, "art_theirs", team_id=OTHER_TID)
    await _seed_history(db_client, "art_mine", event_id="evt_a")
    await _seed_history(db_client, "art_theirs", event_id="evt_a")

    assert await _team_artifact_turns(db_client, TID) == {"evt_a": ["art_mine"]}


@pytest.mark.asyncio
async def test_turn_map_excludes_private_artifacts(db_client):
    from backend.routes.teams import _team_artifact_turns

    await _seed_artifact(db_client, "art_private", team_id=None)
    await _seed_history(db_client, "art_private", event_id="evt_a")

    assert await _team_artifact_turns(db_client, TID) == {}


@pytest.mark.asyncio
async def test_an_updating_turn_also_gets_a_chip(db_client):
    """Re-registration is how a teammate picks work up, so the turn that
    UPDATED an artifact is exactly the one worth surfacing."""
    from backend.routes.teams import _team_artifact_turns

    await _seed_artifact(db_client, "art_1", team_id=TID)
    await _seed_history(db_client, "art_1", event_id="evt_first")
    await db_client.insert("instance_artifact_history", {
        "artifact_id": "art_1", "agent_id": "agent_b", "file_path": "p",
        "action": "updated", "event_id": "evt_second",
    })

    assert await _team_artifact_turns(db_client, TID) == {
        "evt_first": ["art_1"], "evt_second": ["art_1"],
    }


@pytest.mark.asyncio
async def test_rows_without_a_turn_are_skipped(db_client):
    """event_id is nullable — legacy rows and callers with no event in scope
    simply produce no chip rather than a bogus grouping."""
    from backend.routes.teams import _team_artifact_turns

    await _seed_artifact(db_client, "art_1", team_id=TID)
    await _seed_history(db_client, "art_1", event_id=None)

    assert await _team_artifact_turns(db_client, TID) == {}


# ── each switch does what its name says ───────────────────────────────────
#
# The first version had `clear_files` delete the team's artifacts too, on the
# stated grounds that "team artifacts point into the very tree being deleted".
# That is false in the common case: `_resolve_entry` keeps the producer's own
# workspace as the FIRST allowed root, so a team artifact registered the
# ordinary way points there and survives the folder being removed. Deleting
# those rows destroyed pointers to files that still exist.
#
# Artifacts that DID live in the shared folder become broken pointers, which
# is a state this codebase already recovers from (ArtifactService.heal) — a
# far better outcome than silently dropping the ones that were fine.


@pytest.mark.asyncio
async def test_clearing_files_leaves_team_artifacts_alone(db_client, monkeypatch, tmp_path):
    """Content living in the producer's workspace is untouched by removing the
    shared folder, so its artifact row must survive too."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_file(db_client, "f1", team_id=TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_team"}, "clearing FILES must not delete artifacts"
    assert await db_client.execute("SELECT * FROM team_files", fetch=True) == []


@pytest.mark.asyncio
async def test_deleting_a_team_takes_its_workspace_with_it(db_client, monkeypatch, tmp_path):
    """Once the team is gone its artifacts are unreachable by EVERY query path:
    the private surfaces exclude them (team_id IS NULL), list_by_team needs a
    team that no longer exists, and the union joins team_members which the
    delete just emptied. Rows that nothing can ever read are the orphan case
    acceptance #7 is about."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_artifact(db_client, "art_other", team_id=OTHER_TID)
    await _seed_artifact(db_client, "art_private", team_id=None)
    await _seed_history(db_client, "art_team", event_id="evt_a")
    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "keep", team_id=OTHER_TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(
        db_client, team, clear_chat=True, clear_files=True, clear_artifacts=True
    )

    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_other", "art_private"}
    hist = await db_client.execute("SELECT * FROM instance_artifact_history", fetch=True)
    assert hist == []
    files = {r["file_id"] for r in
             await db_client.execute("SELECT * FROM team_files", fetch=True)}
    assert files == {"keep"}


@pytest.mark.asyncio
async def test_clearing_artifacts_alone_keeps_the_files(db_client, monkeypatch, tmp_path):
    """The scopes are independent in both directions."""
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_file(db_client, "f1", team_id=TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(
        db_client, team, clear_chat=False, clear_files=False, clear_artifacts=True
    )

    assert await db_client.execute("SELECT * FROM instance_artifacts", fetch=True) == []
    assert len(await db_client.execute("SELECT * FROM team_files", fetch=True)) == 1


# ── timestamps must carry a timezone ──────────────────────────────────────


@pytest.mark.asyncio
async def test_file_timestamps_are_offset_aware(db_client):
    """`_team_files` returned raw DB rows, so `created_at` reached the browser
    as a naive string ('2026-08-07 12:34:56' from datetime('now'), UTC but
    unmarked). Per the ES spec a date-time with no offset is read as LOCAL
    time, so `Date.parse` in the panel showed a file shared moments ago as
    "8h ago" for a UTC+8 user. The artifacts half never had this because it
    goes through the Artifact model, whose parse_dt attaches UTC.
    """
    from backend.routes.teams import _team_files

    await _seed_file(db_client, "f1", team_id=TID)
    rows = await _team_files(db_client, TID)

    stamp = rows[0]["created_at"]
    assert isinstance(stamp, str), "serialised for the wire"
    assert stamp.endswith("+00:00") or stamp.endswith("Z"), (
        f"timestamp must name its offset, got {stamp!r}"
    )


@pytest.mark.asyncio
async def test_file_rows_do_not_leak_internal_columns(db_client):
    """`SELECT *` put id / owner_user_id / content_hash into the API shape.
    Owner-only, so not a disclosure — but the wire shape should be chosen, not
    inherited from the table."""
    from backend.routes.teams import _team_files

    await _seed_file(db_client, "f1", team_id=TID)
    row = (await _team_files(db_client, TID))[0]

    assert set(row) == {
        "file_id", "original_name", "rel_path", "mime_type", "category",
        "size_bytes", "shared_by_agent_id", "created_at",
    }
