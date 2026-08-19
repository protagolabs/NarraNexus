"""
@file_name: test_gateway_key_misuse_repository_mysql.py
@author: Bin Liang
@date: 2026-08-19
@description: Real-MySQL dialect twin for GatewayKeyMisuseRepository.

This exists to un-hide a false green: the SQLite tests pass on over-length
string fields because SQLite TEXT has no width, but real MySQL VARCHAR columns
reject an over-length insert (error 1406, "Data too long"). So the SQLite suite
alone cannot prove that the endpoint's server-side clipping (route ``_clip``)
lines up with the actual column widths — a clip to the wrong width would still
be a lost row on MySQL. This twin proves, on the MySQL DDL auto_migrate emits:

- a row with EACH str column filled to EXACTLY its width round-trips;
- a row whose fields were over-width, then clipped by the production ``_clip``,
  lands and round-trips at the column width (ties the route clip to the real
  MySQL widths);
- an unresolved event (user_id=None) lands as an alert-only row.

Enable with NARRANEXUS_MYSQL_TEST_URL (same convention as the other *_mysql.py
twins); skipped otherwise.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from backend.routes.admin.gateway_key_misuse import _clip
from xyz_agent_context.repository.gateway_key_misuse_repository import (
    GatewayKeyMisuseRepository,
)
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that a gateway_key_misuse row inserts and round-trips on the real "
        "MySQL dialect, including exactly-column-width and clipped over-width "
        "string fields"
    ),
)

# Distinct key_hash sentinels per test so cleanup is exact and the (key_hash,
# hit_at) dedup index can never make two tests collide.
KH_EXACT = "e" * 256
KH_CLIP = "c" * 256   # what "c" * 900 becomes after _clip(.., 256)
KH_NULL = "nulluid"
KH_DEDUP = "dedup_kh"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    for kh in (KH_EXACT, KH_CLIP, KH_NULL, KH_DEDUP):
        await client.delete("gateway_key_misuse", {"key_hash": kh})
    yield client
    for kh in (KH_EXACT, KH_CLIP, KH_NULL, KH_DEDUP):
        await client.delete("gateway_key_misuse", {"key_hash": kh})
    await client.close()


@pytest.mark.asyncio
async def test_exact_column_width_round_trips(mysql_client):
    repo = GatewayKeyMisuseRepository(mysql_client)
    row_id = await repo.record(
        user_id="u" * 128,
        run_id="r" * 128,
        key_hash=KH_EXACT,          # 256
        caller_ip="i" * 64,
        caller_ua="a" * 256,
        model="m" * 128,
    )
    assert isinstance(row_id, int)

    row = await mysql_client.get_one("gateway_key_misuse", {"key_hash": KH_EXACT})
    assert row is not None
    assert row["user_id"] == "u" * 128
    assert row["run_id"] == "r" * 128
    assert row["key_hash"] == KH_EXACT
    assert row["caller_ip"] == "i" * 64
    assert row["caller_ua"] == "a" * 256
    assert row["model"] == "m" * 128
    assert row["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_over_width_clipped_still_lands(mysql_client):
    """Over-width raw input, clipped by the production ``_clip`` exactly as the
    endpoint does, must land and round-trip on MySQL at the column width."""
    repo = GatewayKeyMisuseRepository(mysql_client)
    row_id = await repo.record(
        user_id="u" * 128,
        run_id=_clip("r" * 500, 128),
        key_hash=_clip("c" * 900, 256),
        caller_ip=_clip("1" * 300, 64),
        caller_ua=_clip("a" * 5000, 256),
        model=_clip("m" * 400, 128),
    )
    assert isinstance(row_id, int)

    row = await mysql_client.get_one("gateway_key_misuse", {"key_hash": KH_CLIP})
    assert row is not None
    assert len(row["run_id"]) == 128
    assert len(row["key_hash"]) == 256
    assert len(row["caller_ip"]) == 64
    assert len(row["caller_ua"]) == 256
    assert len(row["model"]) == 128


@pytest.mark.asyncio
async def test_alert_only_null_user_row_lands(mysql_client):
    """user_id=None (unresolved) still lands as an alert-only row on MySQL."""
    repo = GatewayKeyMisuseRepository(mysql_client)
    row_id = await repo.record(user_id=None, key_hash=KH_NULL, caller_ip="1.2.3.4")
    assert isinstance(row_id, int)

    row = await mysql_client.get_one("gateway_key_misuse", {"key_hash": KH_NULL})
    assert row is not None
    assert row["user_id"] is None
    assert row["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_retry_with_same_hit_at_is_idempotent_on_mysql(mysql_client):
    """The (key_hash, hit_at) UNIQUE index dedups an at-least-once retry on the
    real MySQL DDL: two records with the same anchor collapse to one row and
    return the same id (idempotent success, not a raised duplicate-key error)."""
    repo = GatewayKeyMisuseRepository(mysql_client)
    hit_at = "2026-08-19 10:00:00.000000"

    id1 = await repo.record(user_id="u1", key_hash=KH_DEDUP, hit_at=hit_at, caller_ip="1.1.1.1")
    id2 = await repo.record(user_id="u1", key_hash=KH_DEDUP, hit_at=hit_at, caller_ip="1.1.1.1")

    assert id1 == id2
    rows = await mysql_client.get("gateway_key_misuse", {"key_hash": KH_DEDUP})
    assert len(rows) == 1
