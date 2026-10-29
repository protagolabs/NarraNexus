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
    # MySQL returns an empty tuple where SQLite returns [] — compare emptiness
    assert len(await mysql_client.get(login_ips.TABLE, {"user_id": STALE_UID})) == 0
    assert len(await mysql_client.get(login_ips.TABLE, {"user_id": UID})) == 1
