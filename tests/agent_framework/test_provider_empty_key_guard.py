"""
@file_name: test_provider_empty_key_guard.py
@author: lei.zhou
@date: 2026-08-26
@description: Aggregator provider cards reject empty api_key at the door.

A netmind/yunwu/openrouter card saved with an empty key is guaranteed-broken:
every downstream run fails with provider 401s, and nothing in those errors
points back to "the key you pasted was empty" (the classic cause is an unset
shell variable expanding to "" in a scripted call — observed in production
on 2026-08-25, where the misdiagnosis trail ran through three layers before
reaching the real cause). ``add_provider`` now raises ``ValueError`` at save
time for these card types, which the providers route already maps to a 400.

``netmind_free`` stays exempt — its key is minted by the free-tier
provisioner, not pasted by the user — and non-aggregator card types keep
their existing semantics (claude_oauth legitimately has no key).
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from xyz_agent_context.agent_framework.providers.user_service import (
    UserProviderService,
)


class _FakeDB:
    """Generic in-memory table store (table -> list[dict])."""

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


@pytest.mark.asyncio
@pytest.mark.parametrize("card_type", ["netmind", "yunwu", "openrouter"])
@pytest.mark.parametrize("bad_key", ["", "   "])
async def test_aggregator_card_rejects_empty_key(card_type, bad_key):
    svc = UserProviderService(_FakeDB())
    with pytest.raises(ValueError, match="non-empty api_key"):
        await svc.add_provider(user_id="u1", card_type=card_type, api_key=bad_key)


@pytest.mark.asyncio
async def test_aggregator_card_accepts_real_key():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(
        user_id="u1", card_type="netmind", api_key="nm-real-key",
    )
    # The netmind card creates the linked dual rows (anthropic + openai).
    assert len(new_ids) == 2
    rows = await db.get("user_providers", filters={"user_id": "u1"})
    assert all(r["api_key"] == "nm-real-key" for r in rows)


@pytest.mark.asyncio
async def test_netmind_free_stays_exempt():
    # The free-tier provisioner mints the key itself; an empty key at this
    # layer is not user error and must not start failing.
    svc = UserProviderService(_FakeDB())
    _, new_ids = await svc.add_provider(
        user_id="u1", card_type="netmind_free", api_key="",
    )
    assert len(new_ids) == 2
