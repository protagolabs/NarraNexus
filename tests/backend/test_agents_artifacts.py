"""
@file_name: test_agents_artifacts.py
@author: Bin Liang
@date: 2026-05-08
@description: e2e tests for /api/agents/{agent_id}/artifacts/*

Mounts only the agents_artifacts router on a fresh FastAPI app to avoid
pulling in the full backend.main app surface. Patches get_db_client inside
the route module so handlers receive the test in-memory SQLite client.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact


# ─── helpers ──────────────────────────────────────────────────────────────────


async def _async_return(value):
    """Wraps a value in a coroutine so it can be awaited by route handlers."""
    return value


# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    """
    Provision a fresh FastAPI app mounted with only the artifacts router,
    seed one artifact with a real on-disk file, and patch get_db_client
    so route handlers use the test in-memory SQLite client.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(workspace), raising=False)

    from backend.routes.agents_artifacts import router as artifacts_router
    import backend.routes.agents_artifacts as artifacts_mod

    monkeypatch.setattr(
        artifacts_mod,
        "get_db_client",
        lambda: _async_return(db_client),
    )
    # Also patch the settings reference in the module so the file paths resolve
    # to our tmp workspace rather than the real home dir default.
    monkeypatch.setattr(artifacts_mod, "settings", sa_settings)

    app = FastAPI()
    app.include_router(artifacts_router, prefix="/api/agents")

    # Seed one artifact
    repo = ArtifactRepository(db_client)
    art = Artifact(
        artifact_id="art_99999999",
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        title="hello",
        kind="text/html",
        latest_version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    folder = workspace / "agent_x_user_y/artifacts/art_99999999"
    folder.mkdir(parents=True)
    file_path_abs = folder / "v1.html"
    file_path_abs.write_text("<p onclick='alert(1)'>hi</p>")
    # Store relative path (relative to workspace) in the DB version row
    rel = str(file_path_abs.relative_to(workspace))
    await repo.create(art, file_path=rel, size_bytes=len(file_path_abs.read_bytes()))

    yield {
        "client": TestClient(app),
        "db": db_client,
        "repo": repo,
        "workspace": workspace,
    }


# ─── test cases ───────────────────────────────────────────────────────────────


def test_list_session_returns_artifact(setup):
    client = setup["client"]
    r = client.get(
        "/api/agents/agent_x/artifacts",
        params={"scope": "session", "session_id": "sess_1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert any(a["artifact_id"] == "art_99999999" for a in body)


def test_list_session_missing_session_id_returns_400(setup):
    client = setup["client"]
    r = client.get("/api/agents/agent_x/artifacts", params={"scope": "session"})
    assert r.status_code == 400


def test_get_detail_includes_versions(setup):
    client = setup["client"]
    r = client.get("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 200
    body = r.json()
    assert body["artifact"]["artifact_id"] == "art_99999999"
    assert len(body["versions"]) == 1


def test_raw_returns_strict_csp(setup):
    client = setup["client"]
    r = client.get("/api/agents/agent_x/artifacts/art_99999999/v1/raw")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    # default-src 'none' anchors the policy: anything not explicitly allowed
    # falls back to deny.
    assert "default-src 'none'" in csp
    # text/html artifact must allow inline scripts so interactive demos work,
    # but must NOT allow any external script source — only 'unsafe-inline'.
    assert "script-src 'unsafe-inline'" in csp
    assert "'self'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_pin_clears_session_id(setup):
    client = setup["client"]
    r = client.patch(
        "/api/agents/agent_x/artifacts/art_99999999",
        json={"pinned": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pinned"] is True
    assert body["session_id"] is None

    r = client.get("/api/agents/agent_x/artifacts", params={"scope": "pinned"})
    assert r.status_code == 200
    assert any(a["artifact_id"] == "art_99999999" for a in r.json())


def test_delete_removes_row_and_folder(setup):
    client = setup["client"]
    r = client.delete("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 200
    assert r.json()["deleted"] == "art_99999999"

    r = client.get("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 404

    folder = setup["workspace"] / "agent_x_user_y/artifacts/art_99999999"
    assert not folder.exists()


def test_other_agents_cannot_read(setup):
    client = setup["client"]
    r = client.get("/api/agents/AGENT_OTHER/artifacts/art_99999999")
    assert r.status_code == 404


def test_non_owner_jwt_returns_403(monkeypatch, tmp_path):
    """C1: Cloud-mode — JWT user_id that does not match agent's created_by gets 403."""
    import asyncio

    workspace = tmp_path / "workspace2"
    workspace.mkdir()

    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(workspace), raising=False)

    from backend.routes.agents_artifacts import router as artifacts_router
    import backend.routes.agents_artifacts as artifacts_mod

    # Build a DB with an agent owned by "owner_user"
    from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.utils.schema_registry import auto_migrate

    async def _make_db():
        backend = SQLiteBackend(":memory:")
        await backend.initialize()
        await auto_migrate(backend)
        return await AsyncDatabaseClient.create_with_backend(backend)

    db = asyncio.run(_make_db())

    # Insert agent owned by "owner_user"
    asyncio.run(db.insert("agents", {
        "agent_id": "agent_owned",
        "agent_name": "Test Agent",
        "created_by": "owner_user",
    }))

    monkeypatch.setattr(artifacts_mod, "get_db_client", lambda: _async_return(db))
    monkeypatch.setattr(artifacts_mod, "settings", sa_settings)

    app = FastAPI()
    # Inject a middleware that sets request.state.user_id to simulate cloud JWT
    from starlette.middleware.base import BaseHTTPMiddleware

    class FakeJWTMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user_id = "other_user"  # Not the owner
            return await call_next(request)

    app.add_middleware(FakeJWTMiddleware)
    app.include_router(artifacts_router, prefix="/api/agents")

    client = TestClient(app, raise_server_exceptions=False)
    # list_artifacts — should get 403
    r = client.get("/api/agents/agent_owned/artifacts", params={"scope": "pinned"})
    assert r.status_code == 403

    # Local-mode (no user_id in state) should pass through
    app2 = FastAPI()
    app2.include_router(artifacts_router, prefix="/api/agents")
    client2 = TestClient(app2, raise_server_exceptions=False)
    # No agent in DB with agent_id "agent_owned" in local mode — will hit 404 from art lookup, not 403
    r2 = client2.get("/api/agents/agent_owned/artifacts", params={"scope": "pinned"})
    assert r2.status_code == 200  # local mode: ownership not enforced, returns empty list


def test_raw_path_escape_returns_404(setup):
    """C6: Defence-in-depth — if a file_path DB row contains ../, refuse to serve."""
    import asyncio

    client = setup["client"]
    db = setup["db"]

    # Directly update the version row to contain a path-traversal string
    asyncio.run(db.update(
        "instance_artifact_versions",
        {"artifact_id": "art_99999999", "version": 1},
        {"file_path": "../../etc/passwd"},
    ))

    r = client.get("/api/agents/agent_x/artifacts/art_99999999/v1/raw")
    # Must be refused (404) — not 410, not 500
    assert r.status_code == 404


def test_delete_with_already_missing_folder_succeeds(setup):
    """I1: If the artifact folder is already gone, delete still succeeds (DB row is removed)."""
    client = setup["client"]
    workspace = setup["workspace"]

    # Remove the folder manually before issuing DELETE
    import shutil
    folder = workspace / "agent_x_user_y/artifacts/art_99999999"
    if folder.exists():
        shutil.rmtree(folder)

    r = client.delete("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 200
    assert r.json()["deleted"] == "art_99999999"

    # Confirm DB row is gone
    r = client.get("/api/agents/agent_x/artifacts/art_99999999")
    assert r.status_code == 404


def test_create_with_oversize_description_rejected():
    """C5: description longer than 2000 chars is rejected at the Pydantic layer."""
    import pytest
    from pydantic import ValidationError
    from xyz_agent_context.schema.artifact_schema import Artifact
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    huge = "x" * 2001
    with pytest.raises(ValidationError):
        Artifact(
            artifact_id="art_test",
            agent_id="agent_1",
            user_id="user_1",
            session_id="sess_1",
            title="title",
            kind="text/html",
            description=huge,
            created_at=now,
            updated_at=now,
        )


def test_unpin_agent_scoped_artifact_returns_400(setup):
    """
    C1.5: Agent-created artifacts (session_id=null, pinned=true via C1 auto-pin) have
    no original_session_id to restore on unpin. Refuse rather than silently
    drop the artifact into limbo.
    """
    import asyncio
    from datetime import datetime, timezone
    from xyz_agent_context.repository.artifact_repository import ArtifactRepository
    from xyz_agent_context.schema import Artifact

    client = setup["client"]
    repo = ArtifactRepository(setup["db"])

    # Create an agent-scoped artifact (session_id=None, pinned=True, original_session_id=None)
    art = Artifact(
        artifact_id="art_agent999",
        agent_id="agent_x", user_id="user_y",
        session_id=None,                       # agent-scoped from the start
        title="agent-emitted", kind="text/csv",
        pinned=True,
        latest_version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    asyncio.run(repo.create(art, file_path="x", size_bytes=1))

    r = client.patch(
        "/api/agents/agent_x/artifacts/art_agent999",
        json={"pinned": False},
    )
    assert r.status_code == 400
    assert "agent-scoped" in r.text or "DELETE" in r.text
