"""
@file_name: test_office_edit_endpoints.py
@date: 2026-08-19
@description: The two backend halves of office T1 direct editing (spec B §3):

1. POST passthrough on the office-watch proxy — path-allowlisted
   (api/send | api/batch | api/selection), token+port gated, body capped.
   Everything else stays GET-only.
2. POST /{agent_id}/artifacts/{aid}/office-edit-commit — after the watch
   server applied a user's op, this turns it into a commit point: hash
   recompute from disk, history action="user_edited", staged event.
   Idempotent on an unchanged hash.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.office_watch.proxy as owp
from backend.routes.office_watch._token import mint
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _async_return(value):
    return value


# ── proxy POST allowlist ─────────────────────────────────────────────────────


@pytest.fixture
def proxy_client(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.settings.settings.transcription_hmac_secret",
        "test-secret",
        raising=False,
    )
    calls: list = []

    async def fake_post_upstream(user_id, port, path, query, body, content_type):
        calls.append({"user_id": user_id, "port": port, "path": path, "body": body})
        return 200, "application/json", b'{"success": true}'

    monkeypatch.setattr(owp, "_post_upstream", fake_post_upstream)
    app = FastAPI()
    app.include_router(owp.public_router, prefix="/api/public")
    return {"client": TestClient(app), "calls": calls}


def test_post_allowed_path_forwards(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26320/api/batch",
        json=[{"command": "set", "path": "/body/p[1]", "props": {"text": "x"}}],
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}
    assert proxy_client["calls"][0]["path"] == "api/batch"
    assert b'"command"' in proxy_client["calls"][0]["body"]


def test_post_disallowed_path_is_405(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26320/api/shutdown",
        json={},
    )
    assert r.status_code == 405
    assert proxy_client["calls"] == []


def test_post_port_mismatch_is_403(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26321/api/send",
        json={},
    )
    assert r.status_code == 403
    assert proxy_client["calls"] == []


def test_post_oversize_body_is_413(proxy_client):
    token = mint(user_id="user_y", port=26320)
    r = proxy_client["client"].post(
        f"/api/public/office-watch-proxy/{token}/26320/api/batch",
        content=b"x" * (64 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert proxy_client["calls"] == []


# ── office-edit-commit ───────────────────────────────────────────────────────


@pytest.fixture
async def commit_env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from xyz_agent_context.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))
    await db_client.insert("agents", {
        "agent_id": "agent_x", "agent_name": "t", "created_by": "user_y",
    })

    entry = base / WS_REL / "deck.pptx"
    entry.write_bytes(b"pk-v1")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    repo = ArtifactRepository(db_client)
    await repo.create(Artifact(
        artifact_id="art_deck0001", agent_id="agent_x", user_id="user_y",
        session_id="s", title="deck", kind="application/vnd.officecli-live",
        file_path=f"{WS_REL}/deck.pptx", size_bytes=5,
        content_hash=hashlib.sha256(b"pk-v1").hexdigest(),
        created_at=past, updated_at=past,
    ))

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    yield {"client": TestClient(app), "db": db_client, "repo": repo, "entry": entry}


async def _history(db, aid):
    rows = await db.execute(
        "SELECT action FROM instance_artifact_history WHERE artifact_id = %s",
        params=(aid,), fetch=True)
    return [r["action"] for r in rows]


def test_commit_after_watch_edit_updates_hash_history_event(commit_env):
    commit_env["entry"].write_bytes(b"pk-v2-user-edited")
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-edit-commit"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content_hash"] == hashlib.sha256(b"pk-v2-user-edited").hexdigest()

    async def check():
        assert await _history(commit_env["db"], "art_deck0001") == ["user_edited"]
        rows = await commit_env["db"].execute(
            "SELECT payload_json FROM instance_artifact_events WHERE agent_id = %s",
            params=("agent_x",), fetch=True)
        payloads = [json.loads(x["payload_json"]) for x in rows]
        assert [p["action"] for p in payloads] == ["updated"]
    asyncio.get_event_loop().run_until_complete(check())


def test_commit_with_unchanged_hash_is_idempotent(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-edit-commit"
    )
    assert r.status_code == 200
    async def check():
        assert await _history(commit_env["db"], "art_deck0001") == []
    asyncio.get_event_loop().run_until_complete(check())


def test_commit_rejects_non_office_kind(commit_env, tmp_path):
    async def seed():
        (tmp_path / "workspaces" / WS_REL / "notes.md").write_text("# x\n")
        await commit_env["repo"].create(Artifact(
            artifact_id="art_mdxx0001", agent_id="agent_x", user_id="user_y",
            session_id="s", title="notes", kind="text/markdown",
            file_path=f"{WS_REL}/notes.md", size_bytes=4,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))
    asyncio.get_event_loop().run_until_complete(seed())
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_mdxx0001/office-edit-commit"
    )
    assert r.status_code == 400


# ── office-asset upload (T2 image replace) ───────────────────────────────────


def test_asset_upload_lands_next_to_entry(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        files={"file": ("logo.png", b"\x89PNG fake bytes", "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # absolute server path, inside the entry's directory, name randomized
    assert body["path"].startswith(str(commit_env["entry"].parent))
    assert body["path"].endswith(".png")
    assert "logo" in body["path"]
    import pathlib
    assert pathlib.Path(body["path"]).read_bytes() == b"\x89PNG fake bytes"


def test_asset_upload_sanitizes_hostile_filename(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_deck0001/office-asset",
        files={"file": ("../../escape.png", b"x", "image/png")},
    )
    assert r.status_code == 200
    import pathlib
    p = pathlib.Path(r.json()["path"])
    assert p.parent == commit_env["entry"].parent  # never escapes the entry dir


def test_asset_upload_rejects_non_office_artifact(commit_env):
    r = commit_env["client"].post(
        "/api/agents/agent_x/artifacts/art_mdxx0001/office-asset",
        files={"file": ("x.png", b"x", "image/png")},
    )
    assert r.status_code in (400, 404)
