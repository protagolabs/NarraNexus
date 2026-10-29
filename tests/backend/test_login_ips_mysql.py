"""
@file_name: test_login_ips_mysql.py
@author: Bin Liang
@date: 2026-10-29
@description: Real-MySQL twin for the login-IP recorder — the dedup that on MySQL
rests ONLY on idx_login_ips_pair (the DDL generator ignores Column(unique=True)),
the ON DUPLICATE KEY arm advancing last_seen while keeping created_at, and the
retention DELETE. Enable with NARRANEXUS_MYSQL_TEST_URL; skipped otherwise.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

import backend.middleware.login_ips as login_ips
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that instance_login_ips dedups through idx_login_ips_pair, advances "
        "last_seen on the ON DUPLICATE KEY arm and prunes on the real MySQL dialect"
    ),
)

UID = "mysqlloginip" + "0" * 20
STALE_UID = "mysqlloginip" + "1" * 20
IP = "93.184.216.34"


@pytest_asyncio.fixture
async def mysql_client(monkeypatch):
    monkeypatch.setattr(login_ips, "_last_recorded", {})
    monkeypatch.setattr(login_ips, "_last_prune", 0.0)
    monkeypatch.setattr(login_ips, "_background_tasks", set())
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        for uid in (UID, STALE_UID):
            await client.delete(login_ips.TABLE, {"user_id": uid})

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


@pytest.mark.asyncio
async def test_upsert_dedups_and_advances_last_seen_on_mysql(mysql_client, monkeypatch):
    monkeypatch.setattr(login_ips, "wall_clock", lambda: 1_000)
    assert await login_ips.record_login_ip(mysql_client, UID, IP) is True
    first = (await mysql_client.get(login_ips.TABLE, {"user_id": UID}))[0]

    monkeypatch.setattr(login_ips, "wall_clock", lambda: 2_000)
    assert await login_ips.record_login_ip(mysql_client, UID, IP) is True

    rows = await mysql_client.get(login_ips.TABLE, {"user_id": UID})
    assert len(rows) == 1                                # ON DUPLICATE KEY hit the index
    assert int(rows[0]["last_seen"]) == 2_000
    assert rows[0]["created_at"] == first["created_at"]  # first-seen preserved


@pytest.mark.asyncio
async def test_retention_delete_runs_on_mysql(mysql_client, monkeypatch):
    now = 400 * 86_400
    await mysql_client.insert(login_ips.TABLE, {
        "user_id": STALE_UID, "ip": IP, "pair_key": f"{STALE_UID}|{IP}",
        "last_seen": now - login_ips.RETENTION_SEC - 10,
    })
    monkeypatch.setattr(login_ips, "wall_clock", lambda: now)
    await login_ips.record_login_ip(mysql_client, UID, IP)
    # the retention DELETE runs in the background — wait for it
    await asyncio.gather(*list(login_ips._background_tasks), return_exceptions=True)
    # MySQL returns an empty tuple where SQLite returns [] — compare emptiness
    assert len(await mysql_client.get(login_ips.TABLE, {"user_id": STALE_UID})) == 0
    assert len(await mysql_client.get(login_ips.TABLE, {"user_id": UID})) == 1


@pytest.mark.asyncio
async def test_refused_login_audit_prune_runs_on_mysql(mysql_client, monkeypatch):
    # The DATETIME(6) comparison and the action filter on the real dialect:
    # an old login_refused row is pruned, an old suspend row (admin history)
    # and a fresh refusal are kept.
    monkeypatch.setattr(login_ips, "_last_refused", {})
    old = "2000-01-01 00:00:00"
    try:
        await mysql_client.insert("ban_audit", {"user_id": STALE_UID, "action": "login_refused", "created_at": old})
        await mysql_client.insert("ban_audit", {"user_id": STALE_UID, "action": "suspend", "created_at": old})
        # real clock: the fresh row's created_at is the server's NOW
        await login_ips.record_refused_login(mysql_client, UID, IP, "nimail.cn")
        await asyncio.gather(*list(login_ips._background_tasks))
        stale = await mysql_client.get("ban_audit", {"user_id": STALE_UID})
        assert [r["action"] for r in stale] == ["suspend"]
        fresh = await mysql_client.get("ban_audit", {"user_id": UID, "action": "login_refused"})
        assert len(fresh) == 1 and fresh[0]["evidence_ref"] == f"ip={IP} domain=nimail.cn"
    finally:
        for uid in (UID, STALE_UID):
            await mysql_client.delete("ban_audit", {"user_id": uid})
