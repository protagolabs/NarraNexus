"""
@file_name: test_users_artifacts.py
@author: Bin Liang
@date: 2026-05-09
@description: e2e tests for /api/users/{user_id}/artifacts/* — list, quota, bulk delete.

Mounts only the users_artifacts router on a fresh FastAPI app, mirrors the
isolation pattern in test_agents_artifacts.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact


async def _async_return(value):
    return value


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(workspace), raising=False)

    from backend.routes.users_artifacts import router as users_router
    import backend.routes.users_artifacts as users_mod

    monkeypatch.setattr(users_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(users_mod, "settings", sa_settings)

    app = FastAPI()
    app.include_router(users_router, prefix="/api/users")

    repo = ArtifactRepository(db_client)
    # Seed: 3 artifacts owned by binliang across two agents, 1 owned by other_user
    for aid, agent, owner, title in [
        ("art_u1", "agent_a", "binliang", "first"),
        ("art_u2", "agent_a", "binliang", "second"),
        ("art_u3", "agent_b", "binliang", "third"),
        ("art_other", "agent_a", "other_user", "not_yours"),
    ]:
        art = Artifact(
            artifact_id=aid,
            agent_id=agent,
            user_id=owner,
            session_id=None,
            title=title,
            kind="text/csv",
            pinned=True,
            latest_version=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        folder = workspace / f"{agent}_{owner}" / "artifacts" / aid
        folder.mkdir(parents=True)
        f = folder / "data.csv"
        f.write_text("a,b\n1,2\n")
        rel = str(f.relative_to(workspace))
        await repo.create(art, file_path=rel, size_bytes=len(f.read_bytes()))

    yield {"client": TestClient(app), "db": db_client, "repo": repo, "workspace": workspace}


def test_list_returns_only_users_artifacts(setup):
    client = setup["client"]
    r = client.get("/api/users/binliang/artifacts")
    assert r.status_code == 200
    rows = r.json()
    ids = {a["artifact_id"] for a in rows}
    assert ids == {"art_u1", "art_u2", "art_u3"}
    assert "art_other" not in ids


def test_quota_endpoint_returns_usage(setup):
    client = setup["client"]
    r = client.get("/api/users/binliang/artifacts/quota")
    assert r.status_code == 200
    body = r.json()
    assert body["used_count"] == 3
    assert body["count_limit"] in (50, 10)  # local 50 / cloud 10
    assert body["used_bytes"] > 0
    assert body["bytes_limit"] == 100 * 1024 * 1024
    assert body["is_cloud_mode"] in (True, False)


def test_bulk_delete_removes_only_owned(setup):
    client = setup["client"]
    # Try deleting one owned + the other_user artifact in the same call.
    r = client.request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": ["art_u1", "art_other"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert body["skipped_not_owned"] == ["art_other"]

    # Verify state: art_u1 gone, art_other still there
    rows = client.get("/api/users/binliang/artifacts").json()
    ids = {a["artifact_id"] for a in rows}
    assert "art_u1" not in ids
    assert {"art_u2", "art_u3"} <= ids
    # other_user's artifact untouched
    other = client.get("/api/users/other_user/artifacts").json()
    assert any(a["artifact_id"] == "art_other" for a in other)


def test_bulk_delete_empty_body_is_noop(setup):
    client = setup["client"]
    r = client.request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": []},
    )
    assert r.status_code == 200
    assert r.json()["deleted"] == 0


def test_cloud_mode_user_self_check_blocks_other_users(setup, monkeypatch):
    """In cloud mode, a JWT for user A cannot delete user B's artifacts."""
    client = setup["client"]

    # Inject a middleware that simulates a JWT for user 'attacker'
    from starlette.middleware.base import BaseHTTPMiddleware
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routes.users_artifacts import router as users_router
    import backend.routes.users_artifacts as users_mod

    class FakeJWTMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user_id = "attacker"
            return await call_next(request)

    app2 = FastAPI()
    app2.add_middleware(FakeJWTMiddleware)
    app2.include_router(users_router, prefix="/api/users")
    # users_mod was already monkey-patched in the parent fixture to use db_client.

    client2 = TestClient(app2)
    r = client2.get("/api/users/binliang/artifacts")
    assert r.status_code == 403

    r = client2.request(
        "DELETE",
        "/api/users/binliang/artifacts",
        json={"artifact_ids": ["art_u1"]},
    )
    assert r.status_code == 403
