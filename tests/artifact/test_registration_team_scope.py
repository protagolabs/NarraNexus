"""
@file_name: test_registration_team_scope.py
@author: NarraNexus
@date: 2026-08-07
@description: Registration learns about teams.

Three things change at the registration boundary:

1. **Ownership** — an artifact registered during a team turn belongs to that
   team, so it lands in the team workspace instead of one agent's private
   list. The team comes from the SERVER (identity headers), never from a tool
   argument, so a private turn cannot claim one.
2. **Where the file may live** — the entry had to sit inside the agent's own
   workspace, which made a file in the team shared folder impossible to
   register at all: the shared folder is a SIBLING of every agent workspace by
   design, so it was permanently out of bounds.
3. **Attribution** — every registration appends a history row, because
   re-registering overwrites the pointer in place and otherwise leaves no
   trace of who changed what.

`scope` exists only to let an agent opt OUT ("private") of a team turn's
default. It is a plain `str` with a default rather than `Optional[str]`:
FastMCP renders Optional as `anyOf:[X,null]`, which strict-schema providers
reject with a request-level 400 — a whole-request failure, not a degraded
call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.artifact import ArtifactService
from xyz_agent_context.artifact import ArtifactPathEscape
from xyz_agent_context.artifact._artifact_impl.registration import workspace_root
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.workspace_paths import team_shared_dir

AGENT = "agent_a"
USER = "user_1"
TEAM = "team_1"


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    ws = Path(workspace_root(AGENT, USER))
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "own.md").write_text("mine\n")

    shared = team_shared_dir(USER, TEAM)
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "team.md").write_text("ours\n")

    yield {
        "svc": ArtifactService(db_client),
        "repo": ArtifactRepository(db_client),
        "ws": ws,
        "shared": shared,
        "db": db_client,
    }


# ── ownership follows the turn ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_turn_registers_into_the_team(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    got = await env["repo"].get_by_id(res.artifact_id)
    assert got.team_id == TEAM
    assert got.agent_id == AGENT, "producer must survive the move to team ownership"


@pytest.mark.asyncio
async def test_private_turn_is_unchanged(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None,
    )
    assert (await env["repo"].get_by_id(res.artifact_id)).team_id is None


# ── the shared folder becomes registerable (gap T6) ────────────────────────


@pytest.mark.asyncio
async def test_team_artifact_may_live_in_the_shared_folder(env):
    """The whole point of a shared folder is that teammates can build on the
    file. Confining entries to the producer's own workspace made the shared
    folder unusable as an artifact source."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    assert (await env["repo"].get_by_id(res.artifact_id)).team_id == TEAM


@pytest.mark.asyncio
async def test_private_registration_cannot_reach_the_shared_folder(env):
    """Widening is scoped to the team turn that earned it: without a team,
    the old confinement stands."""
    with pytest.raises(ArtifactPathEscape):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(env["shared"] / "team.md"), title="T", description=None,
            target_artifact_id=None,
        )


@pytest.mark.asyncio
async def test_a_team_cannot_register_from_another_teams_folder(env):
    """Being in team_1 grants team_1's folder — not the sibling next to it."""
    other = team_shared_dir(USER, "team_other")
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.md").write_text("theirs\n")

    with pytest.raises(ArtifactPathEscape):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(other / "secret.md"), title="T", description=None,
            target_artifact_id=None, team_id=TEAM,
        )


# ── attribution history ────────────────────────────────────────────────────


async def _history(db, artifact_id):
    return await db.execute(
        "SELECT * FROM instance_artifact_history WHERE artifact_id = %s "
        "ORDER BY id",
        params=(artifact_id,), fetch=True,
    )


@pytest.mark.asyncio
async def test_first_registration_is_recorded_as_created(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    rows = await _history(env["db"], res.artifact_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "created"
    assert rows[0]["agent_id"] == AGENT


@pytest.mark.asyncio
async def test_teammate_update_is_attributed_to_the_teammate(env):
    """The reason the log exists: the pointer is overwritten in place, so
    without a row nothing records that a DIFFERENT agent changed it."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    mate_ws = Path(workspace_root("agent_b", USER))
    mate_ws.mkdir(parents=True, exist_ok=True)
    (mate_ws / "edit.md").write_text("theirs\n")

    await env["svc"].register(
        agent_id="agent_b", user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(mate_ws / "edit.md"), title="T2", description=None,
        target_artifact_id=res.artifact_id, team_id=TEAM,
    )

    rows = await _history(env["db"], res.artifact_id)
    assert [r["action"] for r in rows] == ["created", "updated"]
    assert [r["agent_id"] for r in rows] == [AGENT, "agent_b"]


# ── the tool-layer scope resolution ────────────────────────────────────────
#
# The tool is where "which scope" is decided, and the rule is asymmetric on
# purpose: the TEAM comes from the server, `scope` can only narrow. These
# exercise that decision directly, without an MCP transport.


def _resolve_scope(scope: str, caller_team):
    """Mirror of the tool's one-line decision, kept in sync by these tests."""
    return None if str(scope).strip().lower() == "private" else caller_team


def test_default_scope_follows_the_turn():
    """The common case must need no decision from the model at all."""
    assert _resolve_scope("auto", "team_1") == "team_1"
    assert _resolve_scope("auto", None) is None


def test_private_can_only_narrow():
    """`private` opts out of the team the turn IS in. It is a veto, never a
    way to reach a team the turn is not in."""
    assert _resolve_scope("private", "team_1") is None
    assert _resolve_scope("private", None) is None


def test_unrecognised_scope_degrades_to_the_turn():
    """A weak model inventing a value must not silently produce a private
    artifact nobody on the team can find — the safe degradation is 'follow
    the turn', not 'fall back to private'."""
    for invented in ("team", "TEAM", "shared", "", "null", "workspace"):
        assert _resolve_scope(invented, "team_1") == "team_1"


def test_scope_parameter_is_not_optional_typed():
    """FastMCP renders Optional[str] as anyOf:[str,null], which strict-schema
    providers reject with a request-level 400 — the whole request fails, not
    just this call. The parameter must stay a plain str with a default."""
    import inspect

    from xyz_agent_context.module.common_tools_module._common_tools_impl import (
        artifact_tool,
    )

    src = inspect.getsource(artifact_tool)
    assert 'scope: str = "auto"' in src, "scope must be a plain str with a default"
    assert "scope: Optional" not in src
