"""
@file_name: test_steer_inbox_mysql.py
@author: Bin Liang
@date: 2026-08-21
@description: Real-MySQL dialect twin for SteerInboxRepository's hand-written
SQL (iron rule: raw SQL gets a MySQL twin, not just the SQLite suite).

The SQLite suite proves nothing about the only dialect dev/prod run — and
the append pre-check + unique-index dedup, the `consumed_at IS NULL` pull,
and the `id <= cursor` scoped consume are exactly the shapes a dialect gap
would hide. Enable with NARRANEXUS_MYSQL_TEST_URL; skipped otherwise.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from xyz_agent_context.repository.steer_inbox_repository import SteerInboxRepository
from xyz_agent_context.schema.steer_schema import SteerInjection
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that steer_inbox's append dedup (unique index), the consumed_at "
        "IS NULL pull and the id <= cursor scoped consume parse and behave "
        "on the real MySQL dialect"
    ),
)

RUN_A = "run_mysql_twin_a"
RUN_B = "run_mysql_twin_b"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        for run in (RUN_A, RUN_B):
            await client.execute(
                "DELETE FROM steer_inbox WHERE run_id = %s", params=(run,)
            )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


def _inj(run_id: str, msg_id: str, content: str = "x") -> SteerInjection:
    return SteerInjection(
        run_id=run_id, msg_id=msg_id, role="user", content=content,
        sender_id="agent_sender", source="team",
    )


@pytest.mark.asyncio
async def test_append_dedup_pull_and_scoped_consume_roundtrip(mysql_client):
    repo = SteerInboxRepository(mysql_client)

    assert await repo.append(_inj(RUN_A, "m1", "first")) is True
    assert await repo.append(_inj(RUN_A, "m1", "dup")) is False  # unique index
    assert await repo.append(_inj(RUN_A, "m2", "second")) is True
    assert await repo.append(_inj(RUN_B, "m1", "other-run")) is True

    pending = await repo.pull_unconsumed(RUN_A)
    assert [p.content for p in pending] == ["first", "second"]  # FIFO, run-scoped

    consumed = await repo.mark_consumed(RUN_A, pending[0].id)
    assert consumed == 1
    assert [p.msg_id for p in await repo.pull_unconsumed(RUN_A)] == ["m2"]

    # consuming RUN_A left RUN_B untouched
    assert len(await repo.pull_unconsumed(RUN_B)) == 1
