"""
@file_name: test_awareness_update_time.py
@author:
@date: 2026-08-12
@description: Pin that awareness upsert stamps updated_at (Mark's item 12).

`InstanceAwarenessRepository.upsert` updated the existing row with only
{"awareness": ...}, so `updated_at` stayed frozen at creation time and any
"updated at" UI / sort was wrong. This pins that the update path carries
`updated_at`.
"""
from __future__ import annotations

from xyz_agent_context.repository.instance_awareness_repository import (
    InstanceAwarenessRepository,
)


class _FakeDB:
    def __init__(self):
        self.update_calls = []

    async def get(self, table, filters=None, **_):
        # An existing row so upsert takes the UPDATE branch.
        return [{"id": 1, "instance_id": filters["instance_id"], "awareness": "old"}]

    async def update(self, table, filters, data):
        self.update_calls.append(data)
        return 1


async def test_upsert_update_stamps_updated_at():
    db = _FakeDB()
    repo = InstanceAwarenessRepository(db)
    ok = await repo.upsert("inst_1", "new awareness")
    assert ok is True
    assert db.update_calls, "expected an UPDATE on an existing row"
    assert "updated_at" in db.update_calls[-1]
