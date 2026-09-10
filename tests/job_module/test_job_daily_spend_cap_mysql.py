"""
@file_name: test_job_daily_spend_cap_mysql.py
@author: Bin Liang
@date: 2026-09-09
@description: Real-MySQL dialect twin for `_daily_spend_usd_for_user`'s raw
SQL (B-14). `%s` placeholders + an aggregate `COALESCE(SUM(...))` read
correctly under SQLite's rewritten-text execution path regardless of
dialect-specific quirks; this twin proves the same query against real MySQL.

Enable with NARRANEXUS_MYSQL_TEST_URL (same convention as the project's other
*_mysql.py twins — see tests/mysql_dialect.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_tz

import pytest
import pytest_asyncio

from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus_plugins.job_module.job_trigger import _daily_spend_usd_for_user
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the daily-spend-cap aggregate query "
        "(COALESCE(SUM(total_cost_usd)) WHERE user_id = %s AND created_at >= %s) "
        "runs correctly against the real MySQL dialect"
    ),
)

_PREFIX = "spendcap"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    try:
        await client.execute(
            "DELETE FROM cost_records WHERE user_id LIKE %s", (f"{_PREFIX}%",),
            fetch=False,
        )
    except Exception:  # noqa: BLE001 — teardown must not mask a failure
        pass
    await client.close()


async def _insert_cost_record(db, user_id, cost_usd, created_at=None):
    await db.insert("cost_records", {
        "agent_id": "agent_1",
        "call_type": "agent_loop",
        "model": "test-model",
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_cost_usd": cost_usd,
        "user_id": user_id,
        "created_at": created_at or datetime.now(dt_tz.utc),
    })


@pytest.mark.asyncio
async def test_sums_only_todays_records_for_the_user_on_mysql(mysql_client):
    user = f"{_PREFIX}_u1"
    await _insert_cost_record(mysql_client, user, 1.5)
    await _insert_cost_record(mysql_client, user, 2.25)
    yesterday = datetime.now(dt_tz.utc) - timedelta(days=1)
    await _insert_cost_record(mysql_client, user, 100.0, created_at=yesterday)
    await _insert_cost_record(mysql_client, f"{_PREFIX}_u2", 50.0)

    total = await _daily_spend_usd_for_user(mysql_client, user)

    assert total == pytest.approx(3.75)


@pytest.mark.asyncio
async def test_no_records_returns_zero_on_mysql(mysql_client):
    assert await _daily_spend_usd_for_user(mysql_client, f"{_PREFIX}_nobody") == 0.0
