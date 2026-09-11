"""
@file_name: test_instance_repository_blocked_page_mysql.py
@author: Bin Liang
@date: 2026-09-10
@description: Real-MySQL twin for `InstanceRepository.get_blocked_page`
(review r2 I-A): the `id > %s` keyset predicate, `ORDER BY id` and the inlined
LIMIT on the real dialect. Enable with NARRANEXUS_MYSQL_TEST_URL.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.repository import InstanceRepository
from narranexus.platform.schema.instance_schema import InstanceStatus, ModuleInstanceRecord
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the keyset page behind the BLOCKED reconciliation (id > cursor, "
        "ORDER BY id, inlined LIMIT) behaves on the real MySQL dialect"
    ),
)

_PREFIX = "blockedpage"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.execute(
            "DELETE FROM module_instances WHERE instance_id LIKE %s", (f"{_PREFIX}%",), fetch=False,
        )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


async def _seed(db, name, status):
    await InstanceRepository(db).create_instance(ModuleInstanceRecord(
        instance_id=f"{_PREFIX}_{name}", module_class="JobModule", agent_id="agent_1",
        status=status, dependencies=["x"],
    ))


async def _mine(repo, after, limit):
    # The shared test database may hold other BLOCKED rows; keep only ours.
    return [r for r in await repo.get_blocked_page(after, limit) if r.instance_id.startswith(_PREFIX)]


@pytest.mark.asyncio
async def test_keyset_walk_on_mysql(mysql_client):
    for i in range(5):
        await _seed(mysql_client, f"b{i}", InstanceStatus.BLOCKED)
    await _seed(mysql_client, "active", InstanceStatus.ACTIVE)
    repo = InstanceRepository(mysql_client)

    first_ours = (await repo.get_by_instance_id(f"{_PREFIX}_b0")).id
    seen, after = [], first_ours - 1
    while True:
        page = await repo.get_blocked_page(after, 2)
        if not page:
            break
        assert len(page) <= 2
        assert [r.id for r in page] == sorted(r.id for r in page)
        assert all(r.id > after for r in page)
        seen += [r.instance_id for r in page if r.instance_id.startswith(_PREFIX)]
        # Rows leaving the BLOCKED set mid-walk must not shift the next page.
        if page[0].instance_id.startswith(_PREFIX):
            await repo.update_status(page[0].instance_id, InstanceStatus.ACTIVE)
        after = page[-1].id

    assert seen == [f"{_PREFIX}_b{i}" for i in range(5)]
    assert await _mine(repo, after, 10) == []
