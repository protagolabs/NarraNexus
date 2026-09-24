"""
@file_name: test_approval_store.py
@author:
@date: 2026-09-22
@description: Tests for the cross-process approval store.

Why this exists at all, stated plainly because it was a design miss: the
browser session runs in the **MCP host process**, and the UI that answers its
questions runs in the **backend process**. An in-memory registry lives in one
and is invisible to the other, so the agent raised a prompt nobody could ever
see and waited for an answer that could not arrive. Verified live on
2026-09-22: the MCP log showed `browser session up for agent_…`, the backend
answered `GET /api/browser/approvals/agent_… → {"pending":[]}`.

So the store is the database — not for durability, but because it is the only
thing both processes can see.
"""
from __future__ import annotations

import pytest

from narranexus.platform.browser._browser_impl.approval_store import ApprovalStore


class FakeDb:
    """Minimal stand-in for AsyncDatabaseClient's row API."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}

    async def get_one(self, table, filters):
        for row in self.tables.get(table, []):
            if all(row.get(k) == v for k, v in filters.items()):
                return dict(row)
        return None

    async def get(self, table, filters=None, order_by=None, **_kw):
        # Mirrors AsyncDatabaseClient.get — named `get`, not `get_all`. The
        # first version of this fake invented `get_all`, so the tests passed
        # against an API the real client does not have and the feature failed
        # the moment it touched a database.
        out = [dict(r) for r in self.tables.get(table, [])
               if not filters or all(r.get(k) == v for k, v in filters.items())]
        if order_by:
            out.sort(key=lambda r: str(r.get(order_by) or ""))
        return out

    async def insert(self, table, data):
        self.tables.setdefault(table, []).append(dict(data))

    async def delete(self, table, filters):
        rows = self.tables.get(table, [])
        before = len(rows)
        self.tables[table] = [r for r in rows
                     if not all(r.get(k) == v for k, v in filters.items())]
        return before - len(self.tables[table])


def store() -> tuple[ApprovalStore, FakeDb]:
    db = FakeDb()
    return ApprovalStore(db), db


@pytest.mark.asyncio
async def test_a_request_is_visible_to_a_reader_that_never_saw_it_created():
    """The whole point: the process that asks and the process that answers are
    different ones, so the question has to outlive the object that made it."""
    writer, db = store()
    await writer.request(agent_id="a1", origin="https://x.example", capability="downloads",
                         turn_id="t", thread_id="th")

    # A completely separate instance — as the backend process would be.
    reader = ApprovalStore(db)
    pending = await reader.pending("a1")

    assert [p["origin"] for p in pending] == ["https://x.example"]


@pytest.mark.asyncio
async def test_asking_twice_reuses_the_row():
    s, _db = store()
    first = await s.request(agent_id="a1", origin="https://x.example",
                            capability="downloads", turn_id="t", thread_id="th")
    second = await s.request(agent_id="a1", origin="https://x.example",
                             capability="downloads", turn_id="t", thread_id="th")

    assert first["approval_id"] == second["approval_id"]
    assert len(await s.pending("a1")) == 1


@pytest.mark.asyncio
async def test_pending_is_scoped_to_one_agent():
    s, _db = store()
    await s.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t", thread_id="th")
    await s.request(agent_id="a2", origin="https://y.example", capability="downloads",
                    turn_id="t", thread_id="th")

    assert len(await s.pending("a1")) == 1


@pytest.mark.asyncio
async def test_different_capabilities_are_different_questions():
    s, _db = store()
    await s.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t", thread_id="th")
    await s.request(agent_id="a1", origin="https://x.example", capability="uploads",
                    turn_id="t", thread_id="th")

    assert len(await s.pending("a1")) == 2


@pytest.mark.asyncio
async def test_taking_a_request_removes_it_so_it_is_answered_once():
    """Two tabs answering the same prompt must not apply two decisions."""
    s, _db = store()
    p = await s.request(agent_id="a1", origin="https://x.example", capability="downloads",
                        turn_id="t", thread_id="th")

    first = await s.take(p["approval_id"])
    second = await s.take(p["approval_id"])

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_taking_an_unknown_id_returns_nothing():
    s, _db = store()
    assert await s.take("appr_nope") is None


@pytest.mark.asyncio
async def test_full_cdp_access_is_refused_before_it_is_ever_stored():
    """Design §6 — the ungated capability is configured, never prompted."""
    s, _db = store()
    with pytest.raises(ValueError, match="full_cdp_access"):
        await s.request(agent_id="a1", origin="https://x.example",
                        capability="full_cdp_access", turn_id="t", thread_id="th")


@pytest.mark.asyncio
async def test_a_database_failure_does_not_break_the_agents_turn():
    """The caller is a navigation. Losing the prompt is bad; turning it into
    an exception mid-turn is worse."""

    class Broken(FakeDb):
        async def insert(self, *_a, **_kw):
            raise RuntimeError("disk full")

    s = ApprovalStore(Broken())
    assert await s.request(agent_id="a1", origin="https://x.example",
                           capability="downloads", turn_id="t", thread_id="th") is None


@pytest.mark.asyncio
async def test_a_database_failure_reading_yields_no_prompts_rather_than_raising():
    class Broken(FakeDb):
        async def get(self, *_a, **_kw):
            raise RuntimeError("gone")

    assert await ApprovalStore(Broken()).pending("a1") == []


@pytest.mark.asyncio
async def test_the_public_shape_uses_the_approval_id_not_the_row_primary_key():
    """Switching this store to the database changed the shape it hands out:
    a raw row carries an auto-increment `id`, and the UI addresses approvals
    by `id`. Shipping the raw row sent every decision to
    `/api/browser/approvals/1` — a request that matched nothing, so the
    prompt sat there and clicking it did nothing. (Observed live 2026-09-22.)
    """
    s, db = store()
    await s.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t", thread_id="th")
    # Simulate the DB assigning a primary key, as a real insert does.
    db.tables["instance_browser_approvals"][0]["id"] = 1

    [row] = await s.pending("a1")

    assert row["id"].startswith("appr_"), "id must address the approval, not the table row"
    assert "approval_id" not in row or row["approval_id"] == row["id"]


@pytest.mark.asyncio
async def test_the_public_shape_carries_what_the_prompt_needs():
    s, _db = store()
    await s.request(agent_id="a1", origin="https://x.example", capability="uploads",
                    turn_id="t", thread_id="th")

    [row] = await s.pending("a1")

    assert row["origin"] == "https://x.example"
    assert row["capability"] == "uploads"
    assert row["agent_id"] == "a1"
