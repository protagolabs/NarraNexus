"""
@file_name: test_instance_repository_blocked_page.py
@author: Bin Liang
@date: 2026-09-10
@description: `InstanceRepository.get_blocked_page` — the keyset page behind
the ModulePoller BLOCKED reconciliation (review r2 I-A).

Only BLOCKED rows, across all agents, strictly after the cursor id, lowest id
first, at most `limit`. Walking pages by the last id visits every BLOCKED row
exactly once even when rows on an earlier page leave the BLOCKED set mid-walk.
The `_mysql` twin runs the same statement on the real dialect.
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository import InstanceRepository
from narranexus.platform.schema.instance_schema import InstanceStatus, ModuleInstanceRecord


async def _seed(db, instance_id, status, agent_id="agent_1"):
    await InstanceRepository(db).create_instance(ModuleInstanceRecord(
        instance_id=instance_id, module_class="JobModule", agent_id=agent_id,
        status=status, dependencies=["x"],
    ))


@pytest.mark.asyncio
async def test_page_holds_only_blocked_rows_in_id_order(db_client):
    await _seed(db_client, "b1", InstanceStatus.BLOCKED)
    await _seed(db_client, "a1", InstanceStatus.ACTIVE)
    await _seed(db_client, "b2", InstanceStatus.BLOCKED, agent_id="agent_2")
    await _seed(db_client, "c1", InstanceStatus.COMPLETED)
    await _seed(db_client, "b3", InstanceStatus.BLOCKED)

    page = await InstanceRepository(db_client).get_blocked_page(0, 10)

    assert [r.instance_id for r in page] == ["b1", "b2", "b3"]
    assert [r.id for r in page] == sorted(r.id for r in page)


@pytest.mark.asyncio
async def test_walk_by_last_id_visits_every_row_once_despite_activation(db_client):
    repo = InstanceRepository(db_client)
    for i in range(5):
        await _seed(db_client, f"b{i}", InstanceStatus.BLOCKED)

    seen, after = [], 0
    while True:
        page = await repo.get_blocked_page(after, 2)
        if not page:
            break
        seen += [r.instance_id for r in page]
        # Rows leaving the BLOCKED set mid-walk must not shift the next page.
        await repo.update_status(page[0].instance_id, InstanceStatus.ACTIVE)
        after = page[-1].id

    assert seen == [f"b{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_limit_and_cursor_bound_the_page(db_client):
    repo = InstanceRepository(db_client)
    for i in range(4):
        await _seed(db_client, f"b{i}", InstanceStatus.BLOCKED)
    first = await repo.get_blocked_page(0, 3)
    assert len(first) == 3
    rest = await repo.get_blocked_page(first[-1].id, 3)
    assert [r.instance_id for r in rest] == ["b3"]
    assert await repo.get_blocked_page(rest[-1].id, 3) == []
