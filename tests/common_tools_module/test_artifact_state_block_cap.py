"""
@file_name: test_artifact_state_block_cap.py
@author: NarraNexus
@date: 2026-08-07
@description: The 'Your registered artifacts' block is capped.

The block used to list EVERY pinned artifact an agent owned, with no limit
and no ordering, on EVERY turn — so an agent that keeps registering grows
its own system prompt without bound (artifacts have no quota). Capping it
to the most recently updated N is the fix; the full set stays reachable
through the artifact tooling, so nothing is lost, it is just not all
carried in-context every turn.

Ordering by updated_at is what makes the cap self-correcting: re-registering
an artifact (the update path) refreshes the timestamp, so whatever the agent
is actively iterating on stays listed and only genuinely cold entries fall
off.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.module.common_tools_module.common_tools_module import (
    ARTIFACT_STATE_BLOCK_LIMIT,
    CommonToolsModule,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _seed(repo, artifact_id, *, age_minutes: int):
    """Seed one pinned artifact whose updated_at is `age_minutes` old."""
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    await repo.create(Artifact(
        artifact_id=artifact_id, agent_id="agent_x", user_id="user_y",
        session_id=None, title=f"T-{artifact_id}", kind="text/markdown",
        pinned=True, file_path=f"{WS_REL}/{artifact_id}.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    repo = ArtifactRepository(db_client)
    mod = CommonToolsModule("agent_x", "user_y", db_client)
    yield {"repo": repo, "mod": mod}


@pytest.mark.asyncio
async def test_block_lists_at_most_the_cap(env):
    """More artifacts than the cap → only the cap's worth are listed."""
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    listed = [f"art_{i:03d}" for i in range(over) if f"art_{i:03d}" in block]
    assert len(listed) == ARTIFACT_STATE_BLOCK_LIMIT, (
        f"block must carry at most {ARTIFACT_STATE_BLOCK_LIMIT} artifacts, got {len(listed)}"
    )


@pytest.mark.asyncio
async def test_block_keeps_the_freshest_not_an_arbitrary_slice(env):
    """The survivors must be the most recently updated ones — that is what
    makes the cap self-correcting for whatever the agent is working on."""
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):  # i == age in minutes → art_000 is the freshest
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    assert "art_000" in block, "the freshest artifact must always be listed"
    oldest = f"art_{over - 1:03d}"
    assert oldest not in block, "the coldest artifact must be the one dropped"


@pytest.mark.asyncio
async def test_under_the_cap_lists_everything(env):
    """The common case is unchanged: below the cap, nothing is hidden."""
    for i in range(3):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    for i in range(3):
        assert f"art_{i:03d}" in block


@pytest.mark.asyncio
async def test_repository_default_is_still_unlimited(env):
    """`list_pinned()` without a limit must stay exhaustive — bootstrap's
    duplicate check (bootstrap/profiles.py) scans it and would silently
    re-create profile artifacts if the repository started truncating."""
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    assert len(await env["repo"].list_pinned("agent_x")) == over
