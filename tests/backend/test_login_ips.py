"""
@file_name: test_login_ips.py
@author: Bin Liang
@date: 2026-10-29
@description: The recorder behind instance_login_ips — which public IPs each user
is actively authenticating from, for the sentinel's abuse IP<->account linkage.

Two write paths share one recorder:
- login (netmind-login) records unconditionally;
- every authenticated request notes (user, ip), throttled per pair, written in
  the background — so a JWT minted on one IP and replayed from another (the
  scanner's VPS) still lands in the table, and last_seen tracks real activity
  rather than the 7-day token's mint time.
Non-public IPs never enter the table (a proxy-hop misconfig would otherwise
fill it with nginx's address and silently blind the linkage). Idle rows are
pruned after the retention period.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

import backend.auth as auth_mod
import backend.middleware.login_ips as login_ips
from backend.auth import auth_middleware, create_token

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "1.1.1.1"
UID = "a" * 32


@pytest.fixture(autouse=True)
def _fresh_recorder_state(monkeypatch):
    """The throttle map / prune clock are module state; isolate every test."""
    monkeypatch.setattr(login_ips, "_last_recorded", {})
    monkeypatch.setattr(login_ips, "_last_refused", {})
    monkeypatch.setattr(login_ips, "_last_prune", 0.0)
    monkeypatch.setattr(login_ips, "_background_tasks", set())


def _clock(monkeypatch, t):
    monkeypatch.setattr(login_ips, "wall_clock", lambda: t)


async def _rows(db_client, user_id=UID):
    return await db_client.get(login_ips.TABLE, {"user_id": user_id})


# ─────────────────────────── record_login_ip ───────────────────────────

@pytest.mark.asyncio
async def test_record_writes_row_with_now(db_client, monkeypatch):
    _clock(monkeypatch, 1_000)
    assert await login_ips.record_login_ip(db_client, UID, PUBLIC_IP) is True
    rows = await _rows(db_client)
    assert len(rows) == 1
    assert rows[0]["ip"] == PUBLIC_IP
    assert rows[0]["last_seen"] == 1_000
    assert rows[0]["pair_key"] == f"{UID}|{PUBLIC_IP}"


@pytest.mark.asyncio
async def test_same_pair_dedups_and_strictly_bumps_last_seen(db_client, monkeypatch):
    _clock(monkeypatch, 1_000)
    await login_ips.record_login_ip(db_client, UID, PUBLIC_IP)
    first = (await _rows(db_client))[0]
    _clock(monkeypatch, 2_000)
    await login_ips.record_login_ip(db_client, UID, PUBLIC_IP)
    rows = await _rows(db_client)
    assert len(rows) == 1                               # same (user, ip) → one row
    assert rows[0]["last_seen"] == 2_000                # the conflict arm DID update
    assert rows[0]["created_at"] == first["created_at"]  # first-seen time preserved


@pytest.mark.asyncio
async def test_distinct_ips_make_distinct_rows(db_client, monkeypatch):
    _clock(monkeypatch, 1_000)
    await login_ips.record_login_ip(db_client, UID, PUBLIC_IP)
    await login_ips.record_login_ip(db_client, UID, OTHER_PUBLIC_IP)
    assert {r["ip"] for r in await _rows(db_client)} == {PUBLIC_IP, OTHER_PUBLIC_IP}


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["10.0.0.5", "172.18.0.4", "127.0.0.1", "100.64.0.9", "-", "testclient", ""])
async def test_non_public_ip_is_never_recorded(db_client, monkeypatch, ip):
    _clock(monkeypatch, 1_000)
    assert await login_ips.record_login_ip(db_client, UID, ip) is False
    assert await _rows(db_client) == []


@pytest.mark.asyncio
async def test_db_failure_is_swallowed(monkeypatch):
    class _Boom:
        async def upsert(self, *a, **k):
            raise RuntimeError("db gone")

        async def execute(self, *a, **k):
            raise RuntimeError("db gone")

    from loguru import logger

    _clock(monkeypatch, 1_000)
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        assert await login_ips.record_login_ip(_Boom(), UID, PUBLIC_IP) is False  # no raise
    finally:
        logger.remove(sink)
    # the cause must reach the log (loguru ignores stdlib's exc_info=True)
    assert any("record failed" in ln and "db gone" in ln for ln in lines)


@pytest.mark.asyncio
async def test_idle_rows_are_pruned_after_retention(db_client, monkeypatch):
    now = 100 * 86_400
    stale = now - login_ips.RETENTION_SEC - 10
    await db_client.insert(login_ips.TABLE, {
        "user_id": "b" * 32, "ip": OTHER_PUBLIC_IP,
        "pair_key": f"{'b' * 32}|{OTHER_PUBLIC_IP}", "last_seen": stale,
    })
    _clock(monkeypatch, now)
    await login_ips.record_login_ip(db_client, UID, PUBLIC_IP)
    await _drain()                                       # prune runs in the background
    assert await _rows(db_client, "b" * 32) == []        # idle past retention → gone
    assert len(await _rows(db_client)) == 1              # the fresh row stays


@pytest.mark.asyncio
async def test_prune_runs_at_most_once_per_interval(db_client, monkeypatch):
    calls = []
    real_execute = db_client.execute

    async def _spy(sql, *a, **k):
        if "DELETE" in sql:
            calls.append(sql)
        return await real_execute(sql, *a, **k)

    monkeypatch.setattr(db_client, "execute", _spy)
    _clock(monkeypatch, 10 * 86_400)
    await login_ips.record_login_ip(db_client, UID, PUBLIC_IP)
    await login_ips.record_login_ip(db_client, UID, OTHER_PUBLIC_IP)
    await _drain()
    # one prune run = one DELETE per retained table, not one per call
    assert len([c for c in calls if login_ips.TABLE in c]) == 1
    assert len(calls) == 2


def test_retention_is_a_safe_lower_bound_for_the_reader():
    # A LOWER-BOUND sentinel on this repo's own constant only. The real
    # cross-repo check is the reader's: nexus_sentinel's link_window_problem()
    # refuses (logs an error at boot) any ACCESS_LINK_WINDOW_SEC outside
    # (RECORD_THROTTLE_SEC, RETENTION_SEC), mirroring these two values.
    assert login_ips.RETENTION_SEC >= 7 * 86_400
    assert login_ips.RECORD_THROTTLE_SEC <= 600


# ─────────────────────── note_authenticated_request ───────────────────────

def _wire(monkeypatch, db_client):
    import narranexus.platform.utils.db.db_factory as db_factory

    async def _ret():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", lambda: _ret())


async def _drain():
    """Wait for scheduled background writes. return_exceptions: a failed write
    is the recorder's done-callback's business (it logs), not the awaiter's."""
    await asyncio.gather(*list(login_ips._background_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_note_records_in_background_then_throttles(db_client, monkeypatch):
    _wire(monkeypatch, db_client)
    _clock(monkeypatch, 1_000)
    login_ips.note_authenticated_request(UID, PUBLIC_IP)
    await _drain()
    assert (await _rows(db_client))[0]["last_seen"] == 1_000

    _clock(monkeypatch, 1_000 + login_ips.RECORD_THROTTLE_SEC - 1)
    login_ips.note_authenticated_request(UID, PUBLIC_IP)
    assert login_ips._background_tasks == set()           # inside throttle: no write scheduled

    _clock(monkeypatch, 1_000 + login_ips.RECORD_THROTTLE_SEC + 1)
    login_ips.note_authenticated_request(UID, PUBLIC_IP)
    await _drain()
    assert (await _rows(db_client))[0]["last_seen"] == 1_000 + login_ips.RECORD_THROTTLE_SEC + 1


@pytest.mark.asyncio
async def test_note_ignores_non_public_ip(db_client, monkeypatch):
    _wire(monkeypatch, db_client)
    login_ips.note_authenticated_request(UID, "172.18.0.4")
    assert login_ips._background_tasks == set()


@pytest.mark.asyncio
async def test_note_background_failure_never_raises(monkeypatch):
    import narranexus.platform.utils.db.db_factory as db_factory

    async def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(db_factory, "get_db_client", lambda: _boom())
    login_ips.note_authenticated_request(UID, PUBLIC_IP)
    await _drain()  # completes; the failure is logged by the done-callback, not raised


@pytest.mark.asyncio
async def test_throttle_map_stays_bounded(monkeypatch):
    monkeypatch.setattr(login_ips, "_THROTTLE_MAX_ENTRIES", 10)
    _clock(monkeypatch, 1_000)
    for i in range(10):
        login_ips._last_recorded[(f"u{i}", PUBLIC_IP)] = 1.0       # long expired
    login_ips._mark("fresh", PUBLIC_IP, 1_000)
    assert len(login_ips._last_recorded) <= 10
    assert ("fresh", PUBLIC_IP) in login_ips._last_recorded


# ──────────────── end to end through the real auth middleware ────────────────

def _app():
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.state.provider_resolver = None

    @app.get("/api/agents")
    async def list_agents():
        return {"ok": True}

    return app


async def _get(user_id, xff):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t") as ac:
        return await ac.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {create_token(user_id, 'user')}", "x-forwarded-for": xff},
        )


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    # The bound auth provider decodes the JWT through the kernel's WEB_HOST
    # service, normally exposed by importing backend.main; publish it here so
    # this file does not depend on another test having imported the app.
    from backend.plugin_sdk_host import install_web_host

    install_web_host()
    auth_mod._account_state_cache.clear()
    yield
    auth_mod._account_state_cache.clear()


@pytest.mark.asyncio
async def test_authenticated_request_from_a_new_ip_is_recorded(db_client, monkeypatch, cloud):
    # The JWT was minted elsewhere (no login on this IP): the request alone must
    # put (user, ip) in the table — this is the scanner-replays-a-token case.
    _wire(monkeypatch, db_client)
    await db_client.insert("users", {"user_id": UID, "user_type": "individual", "status": "active"})
    resp = await _get(UID, f"{PUBLIC_IP}, 10.0.0.1")
    assert resp.status_code == 200
    await _drain()
    assert [r["ip"] for r in await _rows(db_client)] == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_suspended_user_request_is_not_recorded(db_client, monkeypatch, cloud):
    _wire(monkeypatch, db_client)
    await db_client.insert("users", {"user_id": UID, "user_type": "individual", "status": "banned"})
    resp = await _get(UID, f"{PUBLIC_IP}, 10.0.0.1")
    assert resp.status_code == 403
    await _drain()
    assert await _rows(db_client) == []


@pytest.mark.asyncio
async def test_prune_does_not_block_the_caller(db_client, monkeypatch):
    """The login path awaits record_login_ip; housekeeping must not ride on it."""
    started = asyncio.Event()
    release = asyncio.Event()
    real_execute = db_client.execute

    async def _slow(sql, *a, **k):
        if "DELETE" in sql:
            started.set()
            await release.wait()
        return await real_execute(sql, *a, **k)

    monkeypatch.setattr(db_client, "execute", _slow)
    _clock(monkeypatch, 10 * 86_400)
    await asyncio.wait_for(login_ips.record_login_ip(db_client, UID, PUBLIC_IP), timeout=1)
    await asyncio.wait_for(started.wait(), timeout=1)   # prune did start, in the background
    release.set()
    await _drain()


@pytest.mark.asyncio
async def test_failed_background_write_releases_the_claim(monkeypatch):
    """A transient DB failure must not blind this (user, ip) for the whole
    throttle window — the next request retries."""
    import narranexus.platform.utils.db.db_factory as db_factory

    async def _boom():
        raise RuntimeError("db blip")

    monkeypatch.setattr(db_factory, "get_db_client", lambda: _boom())
    _clock(monkeypatch, 1_000)
    login_ips.note_authenticated_request(UID, PUBLIC_IP)
    await _drain()
    assert (UID, PUBLIC_IP) not in login_ips._last_recorded
    login_ips.note_authenticated_request(UID, PUBLIC_IP)   # retried at once
    assert len(login_ips._background_tasks) == 1
    await _drain()


# ───────────── connection presence (long-lived WebSocket runs) ─────────────

@pytest.mark.asyncio
async def test_presence_refreshes_while_alive_and_stops_after(monkeypatch):
    monkeypatch.setattr(login_ips, "RECORD_THROTTLE_SEC", 0.01)
    noted = []
    monkeypatch.setattr(login_ips, "note_authenticated_request", lambda u, ip: noted.append((u, ip)))
    alive = {"v": True}
    task = login_ips.start_connection_presence(UID, PUBLIC_IP, lambda: alive["v"])
    assert task is not None
    await asyncio.sleep(0.05)
    assert len(noted) >= 2                         # refreshed during the connection
    alive["v"] = False
    await asyncio.wait_for(task, timeout=1)       # exits on its own once closed
    n = len(noted)
    await asyncio.sleep(0.03)
    assert len(noted) == n                         # no refresh after close


def test_presence_not_started_for_non_public_ip():
    assert login_ips.start_connection_presence(UID, "172.18.0.4", lambda: True) is None


# ───────────────── the third auth entry: nx-agent service bearer ─────────────────

def _service_bearer_headers(tmp_path, monkeypatch, user_id):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from narranexus.platform.module_system._mcp_identity import agent_id_headers
    from narranexus.platform.module_system.identity.tokens import ISSUER_BROKER, sign_identity_token

    priv = ed25519.Ed25519PrivateKey.generate()
    pub = tmp_path / "identity_ed25519.pub"
    pub.write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(pub))
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    token = sign_identity_token(user_id, priv_pem, issuer=ISSUER_BROKER)
    return agent_id_headers("agent_39b2b72b823b", user_id=user_id, identity_token=token)


async def _get_service(headers, xff):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t") as ac:
        return await ac.get("/api/agents", headers={**headers, "x-forwarded-for": xff})


@pytest.mark.asyncio
async def test_service_bearer_from_a_public_ip_is_recorded(db_client, monkeypatch, cloud, tmp_path):
    # The 2026-10-28 attacker replayed a sandbox identity token from the internet.
    # That credential is a user-scoped auth entry too and must feed the linkage.
    _wire(monkeypatch, db_client)
    await db_client.insert("users", {"user_id": UID, "user_type": "individual", "status": "active"})
    resp = await _get_service(_service_bearer_headers(tmp_path, monkeypatch, UID), f"{PUBLIC_IP}, 10.0.0.1")
    assert resp.status_code == 200
    await _drain()
    assert [r["ip"] for r in await _rows(db_client)] == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_suspended_service_bearer_is_not_recorded(db_client, monkeypatch, cloud, tmp_path):
    _wire(monkeypatch, db_client)
    await db_client.insert("users", {"user_id": UID, "user_type": "individual", "status": "banned"})
    resp = await _get_service(_service_bearer_headers(tmp_path, monkeypatch, UID), f"{PUBLIC_IP}, 10.0.0.1")
    assert resp.status_code == 403
    await _drain()
    assert await _rows(db_client) == []


# ───────────── refused logins (ban_audit "login_refused" trail) ─────────────

REFUSED = "c" * 32


async def _refusals(db_client, user_id=REFUSED):
    return await db_client.get("ban_audit", {"user_id": user_id, "action": "login_refused"})


@pytest.mark.asyncio
async def test_refused_login_writes_one_audit_row(db_client, monkeypatch):
    _clock(monkeypatch, 1_000)
    await login_ips.record_refused_login(db_client, REFUSED, PUBLIC_IP, "nimail.cn")
    rows = await _refusals(db_client)
    assert len(rows) == 1
    assert rows[0]["evidence_ref"] == f"ip={PUBLIC_IP} domain=nimail.cn"
    assert rows[0]["actor"] == "netmind-login"
    assert rows[0]["reason"] == "blocked email domain"


@pytest.mark.asyncio
async def test_refused_login_is_throttled_per_account_and_ip(db_client, monkeypatch):
    # A replayed blocked-domain token must not turn the audit table into an
    # attacker-driven firehose: one row per (account, ip) per throttle window.
    _clock(monkeypatch, 1_000)
    await login_ips.record_refused_login(db_client, REFUSED, PUBLIC_IP, "nimail.cn")
    await login_ips.record_refused_login(db_client, REFUSED, PUBLIC_IP, "nimail.cn")
    assert len(await _refusals(db_client)) == 1
    await login_ips.record_refused_login(db_client, REFUSED, OTHER_PUBLIC_IP, "nimail.cn")
    assert len(await _refusals(db_client)) == 2
    _clock(monkeypatch, 1_000 + login_ips.RECORD_THROTTLE_SEC + 1)
    await login_ips.record_refused_login(db_client, REFUSED, PUBLIC_IP, "nimail.cn")
    assert len(await _refusals(db_client)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["172.18.0.5", "127.0.0.1", "-"])
async def test_refused_login_neutralises_a_non_public_ip(db_client, monkeypatch, ip):
    # A proxy-hop misconfig would otherwise record nginx's address as evidence.
    # The row is still written: the account + domain + time are worth keeping.
    _clock(monkeypatch, 1_000)
    await login_ips.record_refused_login(db_client, REFUSED, ip, "nimail.cn")
    rows = await _refusals(db_client)
    assert [r["evidence_ref"] for r in rows] == ["ip=- domain=nimail.cn"]


@pytest.mark.asyncio
async def test_refused_login_bounds_the_caller_controlled_domain(db_client, monkeypatch):
    _clock(monkeypatch, 1_000)
    await login_ips.record_refused_login(db_client, REFUSED, PUBLIC_IP, "a" * 5_000)
    evidence = (await _refusals(db_client))[0]["evidence_ref"]
    domain = evidence.split("domain=", 1)[1]
    assert len(domain) == login_ips.MAX_DOMAIN_LEN


@pytest.mark.asyncio
async def test_old_refusals_are_pruned_but_admin_history_is_kept(db_client, monkeypatch):
    now = 100 * 86_400
    old = "1970-01-02 00:00:00"   # far older than now - RETENTION_SEC
    await db_client.insert("ban_audit", {"user_id": "old", "action": "login_refused", "created_at": old})
    await db_client.insert("ban_audit", {"user_id": "old", "action": "suspend", "created_at": old})
    _clock(monkeypatch, now)
    await login_ips.record_refused_login(db_client, REFUSED, PUBLIC_IP, "nimail.cn")
    await _drain()
    old_rows = await db_client.get("ban_audit", {"user_id": "old"})
    assert [r["action"] for r in old_rows] == ["suspend"]   # admin history is never pruned
    assert len(await _refusals(db_client)) == 1              # the fresh refusal stays
