"""The SQLite twin of the 2026-08-17 transaction bug.

MySQL failed loudly (500s, a dead connection, a restart). SQLite has one
connection and nothing to break, so the same defect is silent — and SQLite is
what the desktop/DMG build runs on, which makes "the app said saved and the data
is gone" the user-visible form.

Two defects, same shape as the MySQL ones:

1. `_in_transaction` was an instance flag on a per-event-loop singleton, so any
   task's write took the "already in a transaction, skip commit" branch while an
   unrelated task held a transaction — and disappeared if that transaction rolled
   back.
2. `commit()` cleared the flag only after a successful COMMIT and had no
   `finally`, so one failure left every later write in the process uncommitted.

These tests use a real backend and real rows: the question is always "did the
data survive", never "was a flag set".
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend


async def _backend() -> SQLiteBackend:
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await backend.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
    return backend


async def _keys(backend: SQLiteBackend) -> set[str]:
    return {r["k"] for r in await backend.execute("SELECT k FROM t")}


@pytest.mark.asyncio
async def test_an_unrelated_write_is_not_swallowed_by_someone_elses_rollback():
    """The silent data loss, reproduced.

    Task B's insert is its own unit of work. Before the fix it was folded into
    task A's open transaction and vanished when A rolled back, while B had
    already been told it succeeded.
    """
    backend = await _backend()

    b_started = asyncio.Event()

    async def other():
        b_started.set()
        await backend.insert("t", {"k": "b"})

    await backend.begin_transaction()
    await backend.insert("t", {"k": "a"})

    task = asyncio.create_task(other())
    await b_started.wait()
    await asyncio.sleep(0)  # give B a chance to (wrongly) proceed

    await backend.rollback()
    await task

    keys = await _keys(backend)
    assert "b" in keys, "an unrelated task's write was rolled back with someone else's transaction"
    assert "a" not in keys, "the rolled-back transaction's own row survived"


@pytest.mark.asyncio
async def test_a_failed_commit_does_not_silently_stop_committing_forever():
    """The 'never self-heals' half. One failed COMMIT used to leave the flag
    stuck, after which every write in the process skipped its commit."""
    backend = await _backend()

    await backend.begin_transaction()
    await backend.insert("t", {"k": "x"})

    conn = backend._ensure_conn()
    original_commit = conn.commit

    async def boom():
        raise RuntimeError("disk I/O error")

    conn.commit = boom
    try:
        with pytest.raises(RuntimeError):
            await backend.commit()
    finally:
        conn.commit = original_commit

    # The process is usable again: this write must really land.
    await backend.insert("t", {"k": "y"})
    await conn.rollback()          # anything uncommitted would disappear here
    assert "y" in await _keys(backend)


@pytest.mark.asyncio
async def test_the_write_lock_is_released_even_when_commit_fails():
    """The lock is held for the transaction's duration; losing it on the error
    path would wedge every future write instead of just this one."""
    backend = await _backend()

    await backend.begin_transaction()
    conn = backend._ensure_conn()
    original = conn.commit

    async def boom():
        raise RuntimeError("disk I/O error")

    conn.commit = boom
    try:
        with pytest.raises(RuntimeError):
            await backend.commit()
    finally:
        conn.commit = original

    assert backend._write_lock.locked() is False
    await asyncio.wait_for(backend.insert("t", {"k": "z"}), timeout=2.0)


@pytest.mark.asyncio
async def test_the_owner_can_still_write_inside_its_own_transaction():
    """The lock is not reentrant, so the holder must bypass it — otherwise the
    transaction deadlocks against itself on its first write."""
    backend = await _backend()

    await backend.begin_transaction()
    await asyncio.wait_for(backend.insert("t", {"k": "inside"}), timeout=2.0)
    await backend.commit()

    assert "inside" in await _keys(backend)


@pytest.mark.asyncio
async def test_a_child_task_cannot_end_the_parents_transaction():
    backend = await _backend()

    await backend.begin_transaction()

    async def child():
        with pytest.raises(RuntimeError, match="belongs to the task that opened it"):
            await backend.commit()

    await asyncio.create_task(child())

    await backend.insert("t", {"k": "parent"})
    await backend.commit()
    assert "parent" in await _keys(backend)


@pytest.mark.asyncio
async def test_double_begin_in_the_same_task_is_rejected():
    backend = await _backend()
    await backend.begin_transaction()
    with pytest.raises(RuntimeError, match="Already in a transaction"):
        await backend.begin_transaction()
    await backend.commit()
