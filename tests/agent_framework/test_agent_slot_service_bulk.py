"""
@file_name: test_agent_slot_service_bulk.py
@author:
@date: 2026-08-26
@description: Owner-scoped bulk ops on AgentSlotService — override stats,
    clear-to-inherit across all of an owner's agents, and effective-model
    overview. Mirrors the existing test_agent_slot_service.py _FakeDB idiom
    (equality-filter in-memory store), which matches the db surface these
    methods use (get / get_one / insert / delete).
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from xyz_agent_context.agent_framework.providers.slot_service import AgentSlotService


class _FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get(self, table, filters=None):
        filters = filters or {}
        return [
            r for r in self.tables[table]
            if all(r.get(k) == v for k, v in filters.items())
        ]

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        self.tables[table].append(dict(data))

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        before = len(self.tables[table])
        self.tables[table] = [
            r for r in self.tables[table]
            if not all(r.get(k) == v for k, v in filters.items())
        ]
        return before - len(self.tables[table])


async def _mk_agent(db, agent_id, owner):
    await db.insert("agents", {"agent_id": agent_id, "created_by": owner, "name": agent_id})


async def _mk_override(db, agent_id, slot_name, model="m-x"):
    await db.insert(
        "agent_slots",
        {"agent_id": agent_id, "slot_name": slot_name, "provider_id": "p1",
         "model": model, "params_json": "{}",
         "created_at": "2026-08-26T00:00:00+00:00",
         "updated_at": "2026-08-26T00:00:00+00:00"},
    )


async def _mk_user_slot(db, owner, slot_name, model):
    await db.insert(
        "user_slots",
        {"user_id": owner, "slot_name": slot_name, "provider_id": "p1",
         "model": model, "params_json": "{}", "agent_framework": "nexus_power"},
    )


@pytest.mark.asyncio
async def test_count_owner_overrides_counts_per_slot_own_agents_only():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_agent(db, "a2", "owner1")
    await _mk_agent(db, "a3", "owner1")   # inherits (no override row)
    await _mk_agent(db, "b1", "owner2")   # someone else's
    await _mk_override(db, "a1", "agent")
    await _mk_override(db, "a2", "agent")
    await _mk_override(db, "a2", "helper_llm")
    await _mk_override(db, "b1", "agent")  # must NOT be counted for owner1

    stats = await AgentSlotService(db).count_owner_overrides("owner1")
    assert stats == {"agent": 2, "helper_llm": 1, "total_agents": 3}


@pytest.mark.asyncio
async def test_clear_owner_agents_slot_clears_only_that_slot_own_agents():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_agent(db, "a2", "owner1")
    await _mk_agent(db, "b1", "owner2")
    await _mk_override(db, "a1", "agent")
    await _mk_override(db, "a1", "helper_llm")
    await _mk_override(db, "a2", "agent")
    await _mk_override(db, "b1", "agent")

    cleared = await AgentSlotService(db).clear_owner_agents_slot("owner1", "agent")
    assert cleared == 2  # a1.agent + a2.agent

    # a1.helper_llm untouched
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "helper_llm"}) is not None
    # a1.agent / a2.agent gone
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "agent"}) is None
    assert await db.get_one("agent_slots", {"agent_id": "a2", "slot_name": "agent"}) is None
    # other owner's agent untouched
    assert await db.get_one("agent_slots", {"agent_id": "b1", "slot_name": "agent"}) is not None


@pytest.mark.asyncio
async def test_clear_owner_agents_slot_rejects_bad_slot():
    db = _FakeDB()
    with pytest.raises(ValueError):
        await AgentSlotService(db).clear_owner_agents_slot("owner1", "nope")


@pytest.mark.asyncio
async def test_owner_agents_overview_effective_and_inheriting():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_agent(db, "a2", "owner1")
    await _mk_agent(db, "b1", "owner2")
    await _mk_user_slot(db, "owner1", "agent", "default-agent-model")
    await _mk_user_slot(db, "owner1", "helper_llm", "default-helper-model")
    await _mk_override(db, "a1", "agent", model="pinned-agent-model")  # a1 overrides agent

    overview = await AgentSlotService(db).owner_agents_overview("owner1")

    assert set(overview.keys()) == {"a1", "a2"}          # own agents only
    assert overview["a1"]["agent"] == {"model": "pinned-agent-model", "inheriting": False}
    assert overview["a1"]["helper_llm"] == {"model": "default-helper-model", "inheriting": True}
    assert overview["a2"]["agent"] == {"model": "default-agent-model", "inheriting": True}
