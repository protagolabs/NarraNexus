"""
@file_name: test_artifact_runner.py
@author: Bin Liang
@date: 2026-05-08
@description: artifact_runner persists files to disk + DB and refuses oversized payloads.

Tests cover:
- create_text_artifact: writes correct file path and DB row, returns ArtifactId + URL
- ArtifactTooLarge: raised when content exceeds 1MB
- Iteration: target_artifact_id bumps version, keeps old version files
- ArtifactKindMismatch: raised when iterating with a different kind
- ArtifactPathEscape: raised when upload_binary_artifact receives a path outside workspace
"""

import os

import pytest

from xyz_agent_context.module.common_tools_module._common_tools_impl import artifact_runner
from xyz_agent_context.repository.artifact_repository import ArtifactRepository


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(workspace), raising=False)
    repo = ArtifactRepository(db_client)
    yield {"db": db_client, "repo": repo, "workspace": str(workspace)}


@pytest.mark.asyncio
async def test_create_text_artifact_writes_file_and_row(env):
    repo: ArtifactRepository = env["repo"]
    result = await artifact_runner.create_text_artifact(
        repo=repo,
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        kind="text/csv",
        content="a,b\n1,2\n",
        title="my csv",
        description=None,
        target_artifact_id=None,
    )

    assert result.artifact_id.startswith("art_")
    assert result.version == 1
    assert "/raw" in result.url

    row = await repo.get_by_id(result.artifact_id)
    assert row is not None

    # C2: File naming is now token-based ({hex}.ext), not v{n}.ext.
    # Verify by reading the version row's file_path from DB.
    versions = await repo.list_versions(result.artifact_id)
    assert len(versions) == 1
    v1 = versions[0]
    abs_path = os.path.join(env["workspace"], v1.file_path)
    assert os.path.exists(abs_path)
    assert abs_path.endswith(".csv")  # extension matches kind
    with open(abs_path) as f:
        assert f.read() == "a,b\n1,2\n"


@pytest.mark.asyncio
async def test_create_text_artifact_too_large_raises(env):
    repo: ArtifactRepository = env["repo"]
    huge = "x" * (1024 * 1024 + 1)
    with pytest.raises(artifact_runner.ArtifactTooLarge):
        await artifact_runner.create_text_artifact(
            repo=repo, agent_id="a", user_id="u", session_id="s",
            kind="text/markdown", content=huge, title="t", description=None, target_artifact_id=None,
        )


@pytest.mark.asyncio
async def test_iterate_keeps_old_version_file(env):
    repo: ArtifactRepository = env["repo"]
    r1 = await artifact_runner.create_text_artifact(
        repo=repo, agent_id="a", user_id="u", session_id="s",
        kind="text/markdown", content="v1", title="t", description=None, target_artifact_id=None,
    )
    r2 = await artifact_runner.create_text_artifact(
        repo=repo, agent_id="a", user_id="u", session_id="s",
        kind="text/markdown", content="v2", title="t", description=None, target_artifact_id=r1.artifact_id,
    )
    assert r2.artifact_id == r1.artifact_id
    assert r2.version == 2

    # C2: File naming is now token-based ({hex}.ext), not v{n}.ext.
    # Both version files must exist and be distinct.
    versions = await repo.list_versions(r1.artifact_id)
    assert len(versions) == 2
    v1_path = os.path.join(env["workspace"], versions[0].file_path)
    v2_path = os.path.join(env["workspace"], versions[1].file_path)
    # Both files exist
    assert os.path.exists(v1_path), f"v1 file missing: {v1_path}"
    assert os.path.exists(v2_path), f"v2 file missing: {v2_path}"
    # They are distinct (no collision)
    assert v1_path != v2_path
    # Content matches what was written
    with open(v1_path) as f:
        assert f.read() == "v1"
    with open(v2_path) as f:
        assert f.read() == "v2"


@pytest.mark.asyncio
async def test_iterate_with_kind_mismatch_raises(env):
    repo: ArtifactRepository = env["repo"]
    r1 = await artifact_runner.create_text_artifact(
        repo=repo, agent_id="a", user_id="u", session_id="s",
        kind="text/csv", content="x", title="t", description=None, target_artifact_id=None,
    )
    with pytest.raises(artifact_runner.ArtifactKindMismatch):
        await artifact_runner.create_text_artifact(
            repo=repo, agent_id="a", user_id="u", session_id="s",
            kind="text/html", content="<b>x</b>", title="t", description=None,
            target_artifact_id=r1.artifact_id,
        )


@pytest.mark.asyncio
async def test_upload_binary_path_escape_raises(env):
    repo: ArtifactRepository = env["repo"]
    # local_path outside workspace
    with pytest.raises(artifact_runner.ArtifactPathEscape):
        await artifact_runner.upload_binary_artifact(
            repo=repo, agent_id="a", user_id="u", session_id="s",
            kind="image/png", local_path="/etc/passwd",
            title="t", description=None, target_artifact_id=None,
        )


@pytest.mark.asyncio
async def test_create_without_session_id_auto_pins(env):
    """C1: agent-driven calls (session_id=None) must default to pinned=True so
    the artifact appears in list_pinned instead of being invisible."""
    repo: ArtifactRepository = env["repo"]
    result = await artifact_runner.create_text_artifact(
        repo=repo,
        agent_id="agent_x", user_id="user_y",
        session_id=None,                      # no session — agent-driven path
        kind="text/csv",
        content="a,b\n1,2\n",
        title="agent output", description=None,
        target_artifact_id=None,
    )
    fetched = await repo.get_by_id(result.artifact_id)
    assert fetched is not None
    assert fetched.session_id is None
    assert fetched.pinned is True
    assert fetched.original_session_id is None  # never had one to remember
