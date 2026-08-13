"""
@file_name: test_admin_suspend_route.py
@author: Bin Liang
@date: 2026-08-13
@description: The admin account-suspension mechanism —
POST /api/admin/suspend, POST /api/admin/reinstate,
GET /api/admin/account-state/{user_id}.

A generic, policy-free switch over a user's account state, self-credentialed on
the X-Admin-Secret header (same pattern as migrate-identity). Covers:
- no / wrong X-Admin-Secret -> 403 on every endpoint (never open)
- suspend flips users.status to "banned", writes a ban_audit row, returns
  {suspended, already}
- suspend is idempotent: a second suspend returns already=True and does not
  change the state
- reinstate restores status to "active" and writes a reinstate audit row
- account-state reflects the current status
- opaque reason/evidence_ref are recorded verbatim
- unknown user -> 404
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

SECRET = "test-admin-secret-xyz"
UID = "u_suspend_1"


def _make_app(db_client, monkeypatch, *, secret=SECRET):
    import backend.routes.admin.suspend as mod

    async def _ret(v):
        return v

    monkeypatch.setattr(mod, "get_db_client", lambda: _ret(db_client))
    monkeypatch.setattr(mod.settings, "admin_secret_key", secret)

    app = FastAPI()
    app.include_router(mod.router)
    return app


async def _post(app, path, json=None, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.post(path, json=json, headers=headers)


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.get(path, headers=headers)


async def _seed_user(db_client, user_id=UID, status="active"):
    await db_client.insert(
        "users", {"user_id": user_id, "user_type": "individual", "status": status}
    )


# --------------------------- admin-secret gate ---------------------------

@pytest.mark.asyncio
async def test_suspend_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(app, "/api/admin/suspend", json={"user_id": UID})

    assert resp.status_code == 403
    # untouched
    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_suspend_wrong_secret_rejected(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": "nope"},
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reinstate_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client, status="banned")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(app, "/api/admin/reinstate", json={"user_id": UID})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_account_state_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _get(app, f"/api/admin/account-state/{UID}")

    assert resp.status_code == 403


# --------------------------- suspend behaviour ---------------------------

@pytest.mark.asyncio
async def test_suspend_flips_status_and_writes_audit(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID, "reason": "opaque-note", "evidence_ref": "ref-42",
              "actor": "ops-bot"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"suspended": True, "already": False}

    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "banned"

    audit = await db_client.get("ban_audit", {"user_id": UID})
    assert len(audit) == 1
    assert audit[0]["action"] == "suspend"
    assert audit[0]["reason"] == "opaque-note"
    assert audit[0]["evidence_ref"] == "ref-42"
    assert audit[0]["actor"] == "ops-bot"


@pytest.mark.asyncio
async def test_suspend_is_idempotent(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    first = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )
    second = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    assert first.json() == {"suspended": True, "already": False}
    assert second.json() == {"suspended": True, "already": True}

    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "banned"


@pytest.mark.asyncio
async def test_reinstate_restores_active_and_audits(db_client, monkeypatch):
    await _seed_user(db_client, status="banned")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/reinstate",
        json={"user_id": UID, "actor": "ops-bot"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    assert resp.json() == {"reinstated": True}

    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "active"

    audit = await db_client.get("ban_audit", {"user_id": UID})
    assert any(a["action"] == "reinstate" for a in audit)


@pytest.mark.asyncio
async def test_account_state_reflects_status(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    before = await _get(
        app, f"/api/admin/account-state/{UID}",
        headers={"X-Admin-Secret": SECRET},
    )
    assert before.status_code == 200
    assert before.json() == {"user_id": UID, "status": "active"}

    await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    after = await _get(
        app, f"/api/admin/account-state/{UID}",
        headers={"X-Admin-Secret": SECRET},
    )
    assert after.json() == {"user_id": UID, "status": "banned"}


@pytest.mark.asyncio
async def test_suspend_unknown_user_is_404(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": "ghost"}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_secret_not_configured_is_503(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch, secret="")

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": "anything"},
    )

    assert resp.status_code == 503
