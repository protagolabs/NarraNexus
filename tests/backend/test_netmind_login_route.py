"""
@file_name: test_netmind_login_route.py
@author: NarraNexus
@date: 2026-06-11
@description: e2e tests for POST /api/auth/netmind-login (Phase 1 user-system
unification — "passport for visa" exchange).

Mounts the auth router on a fresh FastAPI app with an in-memory SQLite
db_client; the NetmindAuthClient is monkeypatched so no network is involved.

Covers:
- happy path: verify -> upsert -> own JWT issued (decodable, right claims)
- every login re-runs the (idempotent) free-tier provisioning
- a wallet-service failure does NOT fail the login
- the two provisioners run in ORDER, free tier first, and neither can block
  the other (the race that stranded users on an empty Power account)
- second login: is_new_user=False, no duplicate row
- invalid NetMind token -> HTTP 401; NetMind upstream trouble -> HTTP 502
- power-login guard: unavailable (local, no opt-in) -> 404; available (cloud OR
  local opt-in) -> reachable
- /api/auth/netmind-login is in AUTH_EXEMPT_PATHS (middleware lets it through)
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.integrations.netmind.netmind_auth_client import (
    NetmindAuthError,
    NetmindUpstreamError,
    NetmindUser,
)


_CODE = "c" * 32


class _FakeNetmindClient:
    """Programmable stand-in for NetmindAuthClient."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def verify_token(self, token: str) -> NetmindUser:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _RecordingProvisioner:
    """Stands in for the free-tier provisioner the login path schedules."""

    def __init__(self, fail=False):
        self.fail = fail
        self.seeded = []

    async def __call__(self, user_id: str, **_kw) -> None:
        if self.fail:
            raise RuntimeError("wallet service down")
        self.seeded.append(user_id)


def _make_app(db_client, monkeypatch, netmind_client, *, power_login=True):
    import backend.routes.auth as auth_mod

    async def _async_return(value):
        return value

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    # netmind_login gates on is_power_login_enabled() (the power axis), not the
    # deployment/security axis. Patch that symbol as imported into auth_mod.
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: power_login)
    monkeypatch.setattr(
        auth_mod, "_get_netmind_auth_client", lambda: netmind_client
    )

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


_OK_USER = NetmindUser(
    user_system_code=_CODE,
    email="alice@example.com",
    nickname="Alice",
)


def test_netmind_login_happy_path_issues_own_jwt(db_client, monkeypatch):
    from backend.auth import decode_token

    fake = _FakeNetmindClient(_OK_USER)
    client = _make_app(db_client, monkeypatch, fake)

    resp = client.post(
        "/api/auth/netmind-login", json={"netmind_token": "jwt-from-netmind"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["user_id"] == _CODE
    assert body["is_new_user"] is True
    assert body["display_name"] == "Alice"
    assert body["email"] == "alice@example.com"
    assert fake.calls == 1

    claims = decode_token(body["token"])
    assert claims["user_id"] == _CODE
    assert claims["role"] == "user"


def test_netmind_login_provisions_the_free_tier_on_every_login(db_client, monkeypatch):
    """The wallet is opened at login, and re-checked at every later login.

    Deliberately NOT gated on `is_new`: the provisioner is idempotent (the key
    alias `free::{user_id}` is its own dedup handle, so a second call finds the
    existing wallet rather than opening another), and running it unconditionally
    is what makes a user whose first attempt hit a wallet-service outage
    self-heal on their next sign-in instead of staying broken forever.
    """
    import backend.routes.auth as auth_mod
    import backend.integrations.free_tier.provisioner as prov_mod

    provisioner = _RecordingProvisioner()
    monkeypatch.setattr(prov_mod, "ensure_free_tier_provider", provisioner)
    client = _make_app(db_client, monkeypatch, _FakeNetmindClient(_OK_USER))

    first = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
    second = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert first.json()["is_new_user"] is True
    assert second.json()["is_new_user"] is False
    assert provisioner.seeded == [_CODE, _CODE]
    assert auth_mod is not None


def test_netmind_login_survives_a_broken_wallet_service(db_client, monkeypatch):
    """Login must never fail because the wallet service is down — the user gets
    in, and the next login retries provisioning."""
    import backend.integrations.free_tier.provisioner as prov_mod

    monkeypatch.setattr(
        prov_mod, "ensure_free_tier_provider", _RecordingProvisioner(fail=True)
    )
    client = _make_app(db_client, monkeypatch, _FakeNetmindClient(_OK_USER))

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_netmind_login_invalid_token_is_401(db_client, monkeypatch):
    fake = _FakeNetmindClient(NetmindAuthError("bad token"))
    client = _make_app(db_client, monkeypatch, fake)

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "x"})

    assert resp.status_code == 401


def test_netmind_login_upstream_trouble_is_502(db_client, monkeypatch):
    fake = _FakeNetmindClient(NetmindUpstreamError("netmind down"))
    client = _make_app(db_client, monkeypatch, fake)

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "x"})

    assert resp.status_code == 502


def test_netmind_login_404_when_power_login_disabled(db_client, monkeypatch):
    # Local install with no NARRANEXUS_ENABLE_POWER_LOGIN opt-in.
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER), power_login=False
    )

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 404


def test_netmind_login_reachable_in_local_when_power_login_enabled(db_client, monkeypatch):
    # Dual-mode: a local deployment that opted into Power login can NetMind-login
    # (power_login=True models both cloud and local-opt-in).
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER),
        power_login=True,
    )

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == _CODE


def test_netmind_login_path_is_auth_exempt():
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/netmind-login" in AUTH_EXEMPT_PATHS


def test_netmind_login_schedules_provider_provisioning_in_local(db_client, monkeypatch):
    """The auto-provisioning that mints the two Power providers is wired to fire
    on a LOCAL (power-login-enabled) deployment, not just cloud. We capture the
    fire-and-forget schedule call rather than the background task itself (the
    mint→onboard chain is unit-tested in test_netmind_provisioner.py)."""
    import backend.integrations.netmind.netmind_provisioner as prov_mod

    captured = {}

    async def _capture(user_id, netmind_token, **_kw):
        captured["user_id"] = user_id
        captured["token"] = netmind_token

    # netmind_login imports this symbol inside the function body, so patch it on
    # the source module (not the route module).
    monkeypatch.setattr(prov_mod, "ensure_netmind_provider", _capture)

    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER),
        power_login=True,
    )
    resp = client.post(
        "/api/auth/netmind-login", json={"netmind_token": "tok-123"}
    )

    assert resp.status_code == 200
    assert captured == {"user_id": _CODE, "token": "tok-123"}


@pytest.mark.asyncio
async def test_free_tier_is_provisioned_before_the_users_own_netmind_card(monkeypatch):
    """Order matters, and getting it wrong is silently harmful.

    Both provisioners decide whether to bind the agent/helper slots by asking
    "does this user already have a usable config". Started side by side they
    both read that BEFORE either had written, and the NetMind one — slower,
    because it must mint a key first — finished last and rebound the slots to a
    brand-new Power account with no balance. Every agent call then failed with
    an opaque upstream error while a funded $10 wallet sat unused.

    Awaits the coroutine rather than going through the route: the scheduler is
    fire-and-forget, and TestClient closes its event loop as soon as the
    response is returned, so a task that yields never gets to finish.
    """
    import asyncio

    import backend.integrations.free_tier.provisioner as free_mod
    import backend.integrations.netmind.netmind_provisioner as nm_mod
    from backend.routes.auth import _provision_providers

    order: list[str] = []

    # BOTH fakes must yield. The real provisioners do HTTP and DB work, and it
    # is precisely that suspension point which let a concurrently-started
    # NetMind provisioner observe the pre-write state. A fake that runs to
    # completion without awaiting serializes even under asyncio.gather, so it
    # would pass against the broken version too — verified by reintroducing
    # gather() and watching this test stay green.
    async def _free(user_id, **_kw):
        order.append("free_tier:start")
        await asyncio.sleep(0.01)
        order.append("free_tier:done")

    async def _netmind(user_id, token, **_kw):
        order.append("netmind:start")
        await asyncio.sleep(0.01)
        order.append("netmind:done")

    monkeypatch.setattr(free_mod, "ensure_free_tier_provider", _free)
    monkeypatch.setattr(nm_mod, "ensure_netmind_provider", _netmind)

    await _provision_providers(_CODE, "tok")

    assert order == [
        "free_tier:start", "free_tier:done", "netmind:start", "netmind:done",
    ], order


@pytest.mark.asyncio
async def test_a_failing_free_tier_does_not_block_the_netmind_card(monkeypatch):
    """Chaining must not make one provisioner a single point of failure for the
    other — a wallet-service outage should still leave the user their own key."""
    import backend.integrations.free_tier.provisioner as free_mod
    import backend.integrations.netmind.netmind_provisioner as nm_mod
    from backend.routes.auth import _provision_providers

    ran: list[str] = []

    async def _free(user_id, **_kw):
        raise RuntimeError("wallet service down")

    async def _netmind(user_id, token, **_kw):
        ran.append(user_id)

    monkeypatch.setattr(free_mod, "ensure_free_tier_provider", _free)
    monkeypatch.setattr(nm_mod, "ensure_netmind_provider", _netmind)

    await _provision_providers(_CODE, "tok")
    assert ran == [_CODE]


# ─────────────────── login-IP recording (abuse IP↔account linkage) ───────────
# A successful login records (user_id, client_ip) in instance_login_ips so the
# sentinel can later link a scanning IP to the account(s) behind it and ban both
# together (2026-10-28 incident response). The IP is the one client_ip() sees
# behind the Caddy->nginx chain, so an X-Forwarded-For with 2+ hops is needed
# for the -2nd entry to be the "client".


def _patch_provisioners(monkeypatch):
    """Silence both background provisioners so the login path leaks no network
    task (the known "pytest won't exit" pit)."""
    import backend.integrations.free_tier.provisioner as prov_mod
    import backend.integrations.netmind.netmind_provisioner as nm_prov_mod

    async def _noop(user_id, **_kw):
        return None

    monkeypatch.setattr(prov_mod, "ensure_free_tier_provider", _noop)
    monkeypatch.setattr(nm_prov_mod, "ensure_netmind_provider", _noop)


@pytest.mark.asyncio
async def test_netmind_login_records_client_ip(db_client, monkeypatch):
    _patch_provisioners(monkeypatch)
    client = _make_app(db_client, monkeypatch, _FakeNetmindClient(_OK_USER))

    resp = client.post(
        "/api/auth/netmind-login",
        json={"netmind_token": "t"},
        headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
    )
    assert resp.status_code == 200

    rows = await db_client.get("instance_login_ips", {"user_id": _CODE})
    assert len(rows) == 1
    assert rows[0]["ip"] == "203.0.113.9"
    assert rows[0]["last_seen"] > 0


@pytest.mark.asyncio
async def test_netmind_login_ip_upsert_dedups_and_bumps_last_seen(db_client, monkeypatch):
    _patch_provisioners(monkeypatch)
    client = _make_app(db_client, monkeypatch, _FakeNetmindClient(_OK_USER))
    hdr = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}

    first = client.post("/api/auth/netmind-login", json={"netmind_token": "t"}, headers=hdr)
    assert first.status_code == 200
    row1 = (await db_client.get("instance_login_ips", {"user_id": _CODE}))[0]

    second = client.post("/api/auth/netmind-login", json={"netmind_token": "t"}, headers=hdr)
    assert second.status_code == 200

    rows = await db_client.get("instance_login_ips", {"user_id": _CODE})
    assert len(rows) == 1  # same (user,ip) → one row, not a duplicate
    assert rows[0]["last_seen"] >= row1["last_seen"]


@pytest.mark.asyncio
async def test_netmind_login_distinct_ips_make_distinct_rows(db_client, monkeypatch):
    _patch_provisioners(monkeypatch)
    client = _make_app(db_client, monkeypatch, _FakeNetmindClient(_OK_USER))

    client.post("/api/auth/netmind-login", json={"netmind_token": "t"},
                headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    client.post("/api/auth/netmind-login", json={"netmind_token": "t"},
                headers={"x-forwarded-for": "198.51.100.2, 10.0.0.1"})

    rows = await db_client.get("instance_login_ips", {"user_id": _CODE})
    assert {r["ip"] for r in rows} == {"203.0.113.9", "198.51.100.2"}


@pytest.mark.asyncio
async def test_refused_login_records_no_ip(db_client, monkeypatch):
    """A login refused AFTER the account is known (suspended) must NOT record an
    IP — the upsert sits past every refusal `raise`, not before it."""
    _patch_provisioners(monkeypatch)
    code = _CODE
    await db_client.insert(
        "users", {"user_id": code, "user_type": "individual", "status": "banned"}
    )
    user = NetmindUser(user_system_code=code, email="x@y.z", nickname="X")
    client = _make_app(db_client, monkeypatch, _FakeNetmindClient(user))

    resp = client.post(
        "/api/auth/netmind-login",
        json={"netmind_token": "t"},
        headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
    )
    assert resp.status_code == 403

    rows = await db_client.get("instance_login_ips", {"user_id": code})
    assert rows == []
