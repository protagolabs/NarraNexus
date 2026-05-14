"""
@file_name: test_invite_routes.py
@author: NarraNexus
@date: 2026-05-14
@description: e2e tests for the invite-code flow.

Mounts the invite + auth routers on a fresh FastAPI app, patches
get_db_client / _is_cloud_mode / the mailer / the rate limiters so the
handlers run against an in-memory SQLite client.

Covers: issue, idempotent re-request, Mode-B cap → waitlist,
already-registered short-circuit, and the register() atomic consume.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.invite_code_repository import (
    InviteCodeRepository,
)


async def _async_return(value):
    return value


async def _fake_send_email(*args, **kwargs) -> bool:
    return True


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.invite as invite_mod
    import backend.routes.auth as auth_mod

    # Route handlers reach the test DB.
    monkeypatch.setattr(invite_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    # Force cloud mode so /request and /register are active.
    monkeypatch.setattr(invite_mod, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)

    # Don't actually send email.
    monkeypatch.setattr(invite_mod, "send_email", _fake_send_email)

    # Neutralise the process-local rate limiters for deterministic tests.
    monkeypatch.setattr(invite_mod._ip_limiter, "allow", lambda key: True)
    monkeypatch.setattr(invite_mod._email_limiter, "allow", lambda key: True)

    app = FastAPI()
    app.include_router(invite_mod.router, prefix="/api/invite")
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_request_issues_a_code(client, db_client):
    r = client.post("/api/invite/request", json={"email": "alice@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status"] == "issued"
    # Code is delivered only by email — never echoed in the response.
    assert "code" not in body or body.get("code") is None


@pytest.mark.asyncio
async def test_request_persists_exactly_one_issued_row(db_client, client):
    client.post("/api/invite/request", json={"email": "bob@example.com"})
    repo = InviteCodeRepository(db_client)
    rows = await repo.list_for_email("bob@example.com")
    assert len(rows) == 1
    assert rows[0].status == "issued"
    assert rows[0].email_sent is True


@pytest.mark.asyncio
async def test_repeat_request_resends_same_code_no_new_row(db_client, client):
    client.post("/api/invite/request", json={"email": "carol@example.com"})
    client.post("/api/invite/request", json={"email": "carol@example.com"})
    repo = InviteCodeRepository(db_client)
    rows = await repo.list_for_email("carol@example.com")
    assert len(rows) == 1  # second request did NOT mint a second code


def test_invalid_email_rejected(client):
    r = client.post("/api/invite/request", json={"email": "not-an-email"})
    assert r.status_code == 200
    assert r.json()["success"] is False


@pytest.mark.asyncio
async def test_cap_reached_waitlists(db_client, client, monkeypatch):
    import backend.routes.invite as invite_mod
    monkeypatch.setattr(invite_mod.settings, "invite_auto_issue_cap", 2)

    assert client.post("/api/invite/request", json={"email": "a@example.com"}).json()["status"] == "issued"
    assert client.post("/api/invite/request", json={"email": "b@example.com"}).json()["status"] == "issued"
    third = client.post("/api/invite/request", json={"email": "c@example.com"}).json()
    assert third["status"] == "waitlisted"

    repo = InviteCodeRepository(db_client)
    rows = await repo.list_for_email("c@example.com")
    assert rows[0].status == "waitlisted"
    assert rows[0].email_sent is False  # waitlisted rows are not emailed


@pytest.mark.asyncio
async def test_already_registered_email_short_circuits(db_client, client):
    repo = InviteCodeRepository(db_client)
    used = await repo.create("done@example.com")
    await repo.consume(used.code, "user_done")

    r = client.post("/api/invite/request", json={"email": "done@example.com"})
    assert r.json()["status"] == "already_registered"


@pytest.mark.asyncio
async def test_register_consumes_invite_code(db_client, client):
    client.post("/api/invite/request", json={"email": "newuser@example.com"})
    repo = InviteCodeRepository(db_client)
    code = (await repo.list_for_email("newuser@example.com"))[0].code

    r = client.post(
        "/api/auth/register",
        json={"user_id": "newuser", "password": "secret123", "invite_code": code},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["token"]

    consumed = await repo.get_by_code(code)
    assert consumed.status == "used"
    assert consumed.used_by_user_id == "newuser"


@pytest.mark.asyncio
async def test_register_rejects_reused_code(db_client, client):
    client.post("/api/invite/request", json={"email": "twice@example.com"})
    repo = InviteCodeRepository(db_client)
    code = (await repo.list_for_email("twice@example.com"))[0].code

    first = client.post(
        "/api/auth/register",
        json={"user_id": "userone", "password": "secret123", "invite_code": code},
    )
    assert first.json()["success"] is True

    second = client.post(
        "/api/auth/register",
        json={"user_id": "usertwo", "password": "secret123", "invite_code": code},
    )
    assert second.json()["success"] is False
    assert "used" in second.json()["error"].lower()


def test_register_rejects_unknown_code(client):
    r = client.post(
        "/api/auth/register",
        json={"user_id": "ghost", "password": "secret123", "invite_code": "NX-FAKE0000"},
    )
    assert r.json()["success"] is False
    assert "invalid" in r.json()["error"].lower()
