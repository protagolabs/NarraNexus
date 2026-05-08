"""
@file_name: test_artifact_repository.py
@author: Bin Liang
@date: 2026-05-08
@description: TDD tests for ArtifactRepository.

Uses real in-memory SQLite (via conftest db_client fixture).
Tests: create, iterate, set_pinned, list_by_session, delete cascade, total_bytes_for_agent.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import Artifact


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_artifact(
    artifact_id: str = "art_test0001",
    agent_id: str = "agent_1",
    user_id: str = "user_1",
    session_id: str | None = "ses_abc123",
    title: str = "My Chart",
    kind: str = "text/html",
    pinned: bool = False,
    latest_version: int = 1,
) -> Artifact:
    now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    return Artifact(
        artifact_id=artifact_id,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        title=title,
        kind=kind,
        pinned=pinned,
        latest_version=latest_version,
        created_at=now,
        updated_at=now,
    )


# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(db_client):
    return ArtifactRepository(db_client)


# ─── test cases ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_then_get_returns_same_artifact(repo):
    """Insert one artifact with its first version, fetch by id, fields match."""
    artifact = _make_artifact()
    await repo.create(artifact, file_path="/tmp/art_test0001/v1.html", size_bytes=1024)

    fetched = await repo.get_by_id("art_test0001")
    assert fetched is not None
    assert fetched.artifact_id == "art_test0001"
    assert fetched.agent_id == "agent_1"
    assert fetched.user_id == "user_1"
    assert fetched.session_id == "ses_abc123"
    assert fetched.title == "My Chart"
    assert fetched.kind == "text/html"
    assert fetched.pinned is False
    assert fetched.latest_version == 1

    versions = await repo.list_versions("art_test0001")
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].size_bytes == 1024
    assert versions[0].file_path == "/tmp/art_test0001/v1.html"


@pytest.mark.asyncio
async def test_iterate_increments_version_and_appends_row(repo):
    """create v1, iterate => v2; list_versions returns [1, 2]."""
    artifact = _make_artifact()
    await repo.create(artifact, file_path="/tmp/art/v1.html", size_bytes=500)

    new_version = await repo.iterate(
        "art_test0001",
        file_path="/tmp/art/v2.html",
        size_bytes=600,
    )
    assert new_version == 2

    fetched = await repo.get_by_id("art_test0001")
    assert fetched is not None
    assert fetched.latest_version == 2

    versions = await repo.list_versions("art_test0001")
    assert len(versions) == 2
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].size_bytes == 600


@pytest.mark.asyncio
async def test_pin_clears_session_id(repo):
    """set_pinned(True) sets pinned=True and clears session_id."""
    artifact = _make_artifact(session_id="ses_xyz")
    await repo.create(artifact, file_path="/tmp/art/v1.html", size_bytes=300)

    await repo.set_pinned("art_test0001", pinned=True)

    fetched = await repo.get_by_id("art_test0001")
    assert fetched is not None
    assert fetched.pinned is True
    assert fetched.session_id is None


@pytest.mark.asyncio
async def test_list_by_session_excludes_pinned(repo):
    """Pinned artifact does not show in list_by_session."""
    session_id = "ses_common"
    agent_id = "agent_2"

    art_normal = _make_artifact(
        artifact_id="art_normal01",
        agent_id=agent_id,
        session_id=session_id,
    )
    art_pinned = _make_artifact(
        artifact_id="art_pinned01",
        agent_id=agent_id,
        session_id=session_id,
        pinned=True,
    )

    await repo.create(art_normal, file_path="/tmp/n/v1.html", size_bytes=100)
    await repo.create(art_pinned, file_path="/tmp/p/v1.html", size_bytes=200)

    session_artifacts = await repo.list_by_session(agent_id, session_id)
    artifact_ids = {a.artifact_id for a in session_artifacts}
    assert "art_normal01" in artifact_ids
    assert "art_pinned01" not in artifact_ids

    pinned_artifacts = await repo.list_pinned(agent_id)
    pinned_ids = {a.artifact_id for a in pinned_artifacts}
    assert "art_pinned01" in pinned_ids
    assert "art_normal01" not in pinned_ids


@pytest.mark.asyncio
async def test_delete_cascades_versions(repo):
    """Deleting an artifact removes both the artifact row and its version rows."""
    artifact = _make_artifact()
    await repo.create(artifact, file_path="/tmp/art/v1.html", size_bytes=400)
    await repo.iterate("art_test0001", file_path="/tmp/art/v2.html", size_bytes=450)

    versions_before = await repo.list_versions("art_test0001")
    assert len(versions_before) == 2

    await repo.delete("art_test0001")

    fetched = await repo.get_by_id("art_test0001")
    assert fetched is None

    versions_after = await repo.list_versions("art_test0001")
    assert len(versions_after) == 0


@pytest.mark.asyncio
async def test_total_bytes_for_agent(repo):
    """total_bytes_for_agent sums correctly across all versions of all artifacts."""
    agent_id = "agent_bytes"

    art1 = _make_artifact(artifact_id="art_bytes001", agent_id=agent_id)
    art2 = _make_artifact(artifact_id="art_bytes002", agent_id=agent_id)
    art_other = _make_artifact(artifact_id="art_other001", agent_id="other_agent")

    await repo.create(art1, file_path="/tmp/a1/v1.html", size_bytes=1000)
    await repo.iterate("art_bytes001", file_path="/tmp/a1/v2.html", size_bytes=2000)

    await repo.create(art2, file_path="/tmp/a2/v1.html", size_bytes=500)

    await repo.create(art_other, file_path="/tmp/o/v1.html", size_bytes=9999)

    total = await repo.total_bytes_for_agent(agent_id)
    # art1: 1000 + 2000 = 3000, art2: 500 => total 3500
    assert total == 3500
