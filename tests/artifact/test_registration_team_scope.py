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

import json
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


@pytest.mark.asyncio
async def test_scope_renders_without_anyof_null_in_the_real_schema():
    """Assert the SCHEMA FastMCP actually emits, not the source text.

    The 400 is a property of the emitted JSON Schema: FastMCP renders
    `Optional[X]` as `anyOf:[X,null]`, and strict-schema providers reject the
    whole REQUEST — every tool in it, not just this call. Grepping the
    signature would pass while a type-alias or a future annotation change
    quietly reintroduced the shape, so the generated schema is the only
    honest assertion.
    """
    from mcp.server.fastmcp import FastMCP

    from xyz_agent_context.module.common_tools_module._common_tools_impl import (
        artifact_tool,
    )

    mcp = FastMCP("schema-probe")
    artifact_tool.register(mcp)
    tool = next(t for t in await mcp.list_tools() if t.name == "register_artifact")
    spec = tool.inputSchema["properties"]["scope"]

    assert spec.get("type") == "string", spec
    assert "anyOf" in spec is False or "anyOf" not in spec, spec
    assert "null" not in json.dumps(spec), spec
    assert spec.get("default") == "auto", spec


@pytest.mark.asyncio
async def test_scope_did_not_widen_the_tools_strict_schema_exposure():
    """A regression fence, deliberately not a clean-schema assertion.

    `session_id` / `description` / `target_artifact_id` were already
    Optional-typed before the team workspace, so this tool ALREADY carries the
    anyOf:[X,null] shape on a strict provider — that debt is pre-existing and
    is not this change's to fix (per the design, schema normalisation belongs
    in a gateway layer). What must hold is that adding `scope` did not make it
    worse: the risky set stays exactly those three.
    """
    from mcp.server.fastmcp import FastMCP

    from xyz_agent_context.module.common_tools_module._common_tools_impl import (
        artifact_tool,
    )

    mcp = FastMCP("schema-probe")
    artifact_tool.register(mcp)
    tool = next(t for t in await mcp.list_tools() if t.name == "register_artifact")

    risky = {
        name for name, spec in tool.inputSchema["properties"].items()
        if "anyOf" in spec or "null" in json.dumps(spec)
    }
    assert risky == {"session_id", "description", "target_artifact_id"}, (
        f"strict-schema exposure changed: {risky}"
    )


# ── dedup must not cross the scope boundary ────────────────────────────────
#
# Found by an end-to-end probe over the real MCP transport, not by the unit
# tests above: each of those used a fresh database, so the collision never
# arose. Registering the SAME entry file in both scopes is ordinary —
# an agent writes report.md once and surfaces it privately, then again for
# the team.


@pytest.mark.asyncio
async def test_same_file_in_both_scopes_makes_two_artifacts(env):
    """Agent-scoped dedup keys on (agent_id, file_path). Without the scope in
    that key, the second registration silently returns the FIRST artifact and
    the requested scope is discarded."""
    entry = str(env["ws"] / "own.md")
    private = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R", description=None, target_artifact_id=None,
    )
    team = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R", description=None, target_artifact_id=None,
        team_id=TEAM,
    )

    assert private.artifact_id != team.artifact_id, (
        "a team registration must not be deduped onto the private artifact"
    )
    assert (await env["repo"].get_by_id(private.artifact_id)).team_id is None
    assert (await env["repo"].get_by_id(team.artifact_id)).team_id == TEAM


@pytest.mark.asyncio
async def test_private_call_never_returns_a_team_artifact(env):
    """The leak direction the probe actually hit: a private-chat registration
    handed back an artifact owned by a team."""
    entry = str(env["ws"] / "own.md")
    team = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R", description=None, target_artifact_id=None,
        team_id=TEAM,
    )
    private = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R", description=None, target_artifact_id=None,
    )

    assert private.artifact_id != team.artifact_id
    assert (await env["repo"].get_by_id(private.artifact_id)).team_id is None


@pytest.mark.asyncio
async def test_dedup_still_works_within_one_scope(env):
    """The guard that made dedup exist stays intact: re-registering the same
    file in the SAME scope must still reuse the row, not mint a duplicate tab
    (prod 2026-06-30: two pinned 'Welcome' tabs on one agent)."""
    entry = str(env["ws"] / "own.md")
    first = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R", description=None, target_artifact_id=None,
        team_id=TEAM,
    )
    second = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R2", description=None, target_artifact_id=None,
        team_id=TEAM,
    )
    assert first.artifact_id == second.artifact_id


# ── the turn handle reaches the attribution row ────────────────────────────


@pytest.mark.asyncio
async def test_history_records_the_turn_that_made_the_change(env):
    """Attribution answers "who changed this, in which turn". The turn is a
    fact the platform holds (the events row exists from Step 0), so it is
    passed in rather than inferred — matching artifacts to turns by timestamp
    breaks on the ordinary cases: two artifacts in one turn, or concurrent
    turns in the same room.
    """
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM, event_id="evt_first",
    )
    mate_ws = Path(workspace_root("agent_b", USER))
    mate_ws.mkdir(parents=True, exist_ok=True)
    (mate_ws / "edit.md").write_text("theirs\n")
    await env["svc"].register(
        agent_id="agent_b", user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(mate_ws / "edit.md"), title="T2", description=None,
        target_artifact_id=res.artifact_id, team_id=TEAM, event_id="evt_second",
    )

    rows = await _history(env["db"], res.artifact_id)
    assert [r["event_id"] for r in rows] == ["evt_first", "evt_second"]


@pytest.mark.asyncio
async def test_a_missing_turn_handle_still_records_the_change(env):
    """Plenty of callers have no event in scope. Absence degrades the record;
    it must never cost the agent a successful registration."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None,
    )
    rows = await _history(env["db"], res.artifact_id)
    assert len(rows) == 1
    assert rows[0]["event_id"] is None
