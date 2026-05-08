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
    assert "default-src 'none'" in csp
    # script-src must never be present — no external scripts permitted
    assert "script-src" not in csp
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


def test_ws_emits_pinned_event_when_pin_called(setup):
    """Pin a session-scoped artifact and verify a WS subscriber receives the event."""
    import backend.routes.agents_artifacts as ag_mod
    from backend.routes.artifact_ws import router as ws_router
    from backend.routes.agents_artifacts import router as a_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db = setup["db"]

    async def _ret_db():
        return db

    # Build a fresh app that includes both the REST and WS routers so the
    # same event bus singleton connects them.
    app2 = FastAPI()
    app2.include_router(a_router, prefix="/api/agents")
    app2.include_router(ws_router)

    # The REST router module was already patched in the fixture; that patch
    # remains active for the duration of this test function.

    client2 = TestClient(app2)

    with client2.websocket_connect("/ws/artifacts/agent_x") as ws:
        # Trigger the pinned event via the REST endpoint.
        r = client2.patch(
            "/api/agents/agent_x/artifacts/art_99999999",
            json={"pinned": True},
        )
        assert r.status_code == 200, f"PATCH failed: {r.text}"

        # Drain up to 5 messages; the event should arrive almost immediately.
        # The 30s heartbeat will not fire in this short window, but we guard
        # against it anyway so the test never hangs indefinitely.
        for _ in range(5):
            evt = ws.receive_json()
            if evt.get("type") == "artifact.pinned":
                assert evt["artifact_id"] == "art_99999999"
                assert evt["pinned"] is True
                return
        raise AssertionError("did not receive artifact.pinned event within 5 messages")
