"""
@file_name: test_artifact_content_put.py
@date: 2026-08-19
@description: e2e tests for the user-edit content endpoint (spec A §3.1):

  PUT /api/agents/{agent_id}/artifacts/{artifact_id}/content
      body {content, base_hash}

- 200: file replaced, refreshed Artifact returned (new content_hash).
- 409: stale base_hash → {current_hash} so the editor can re-base.
- 400/413/404/410 map straight from the ArtifactError hierarchy.
- view-token raw route stays read-only — no PUT there.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _async_return(value):
    return value


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    entry = workspace / "notes.md"
    entry.write_text("# old\n", encoding="utf-8")

    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))

    await db_client.insert("agents", {
        "agent_id": "agent_x",
        "agent_name": "Test agent",
        "created_by": "user_y",
    })

    repo = ArtifactRepository(db_client)
    await repo.create(Artifact(
        artifact_id="art_put00001",
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        title="notes",
        kind="text/markdown",
        file_path=f"{WS_REL}/notes.md",
        size_bytes=entry.stat().st_size,
        content_hash=_sha("# old\n"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    yield {"client": TestClient(app), "entry": entry, "repo": repo}


def test_put_content_saves_and_returns_refreshed_artifact(setup):
    client = setup["client"]
    r = client.put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        json={"content": "# new\n", "base_hash": _sha("# old\n")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["artifact_id"] == "art_put00001"
    assert body["content_hash"] == _sha("# new\n")
    assert setup["entry"].read_text(encoding="utf-8") == "# new\n"


def test_put_content_stale_hash_is_409_with_current_hash(setup):
    client = setup["client"]
    r = client.put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        json={"content": "# clobber\n", "base_hash": _sha("stale")},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["current_hash"] == _sha("# old\n")
    assert setup["entry"].read_text(encoding="utf-8") == "# old\n"


def test_put_content_unknown_artifact_is_404(setup):
    r = setup["client"].put(
        "/api/agents/agent_x/artifacts/art_missing0/content",
        json={"content": "x", "base_hash": "y"},
    )
    assert r.status_code == 404


def test_put_content_gone_entry_is_410(setup):
    setup["entry"].unlink()
    r = setup["client"].put(
        "/api/agents/agent_x/artifacts/art_put00001/content",
        json={"content": "# new\n", "base_hash": _sha("# old\n")},
    )
    assert r.status_code == 410
