"""
@file_name: test_sqlite_orphaned_connections.py
@author: NarraNexus
@date: 2026-08-17
@description: A connection whose event loop died must not keep the database
hostage.

The failure this guards (found 2026-08-17 while profiling the suite):

  1. A fire-and-forget task touches the DB — `schedule_user_no_quota_rearm`
     on login is one of several, and the MCP container runs each module on its
     own short-lived threaded loop by design.
  2. The loop closes with that statement still in flight.
  3. aiosqlite's worker thread answers by calling `call_soon_threadsafe` on the
     closed loop, which raises; its `except` handler makes the SAME call again,
     raises again, uncaught — and the worker thread dies holding an open
     sqlite3 connection.
  4. Nobody can close that connection any more, so its lock is held for the
     life of the process. Every later writer waits out `busy_timeout` (30s) on
     each of `_MAX_WRITE_RETRIES` (10) attempts: ~321s, then
     "database is locked".

Six such collisions were 92% of the test suite's 38-minute wall clock, and
every one of them was reported as a PASSING test — which is exactly why this
file exists. The symptom is "slow", so nothing red ever pointed at it.

Both halves are asserted here, because either one alone leaves the hazard: the
worker thread has to survive (only it can close the connection), and
`db_factory` has to actually close what it evicts (nothing else will).

Timing is staged, never slept for: a blocker connection holds the write lock so
the statement is GUARANTEED to still be in flight when its loop closes. A repro
that races is a repro that goes green on its own the first time it is
inconvenient.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import aiosqlite
import pytest

from xyz_agent_context.utils.db import db_backend_sqlite, db_factory
from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend


def _live_threads() -> set[str]:
    return {t.name for t in threading.enumerate() if t.is_alive()}


def test_the_aiosqlite_worker_patch_is_actually_installed():
    """The hardening is a module-import side effect, which is easy to lose in a
    refactor and impossible to notice: losing it costs 5 minutes per collision,
    not an exception."""
    assert (
        aiosqlite.core._connection_worker_thread
        is db_backend_sqlite._resilient_connection_worker_thread
    )


def test_delivering_to_a_closed_loop_is_a_no_op_not_a_raise():
    """Upstream re-raises from its own `except` handler, which is what kills the
    thread. Dropping the result is correct — the coroutine that was awaiting it
    died with its loop."""
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    loop.close()

    db_backend_sqlite._deliver_to_origin_loop(
        future, aiosqlite.core.set_result, "ignored"
    )
    db_backend_sqlite._deliver_to_origin_loop(
        future, aiosqlite.core.set_exception, RuntimeError("ignored")
    )


def test_a_write_abandoned_by_its_loop_leaves_the_connection_closable(tmp_path):
    """The whole failure on one database file, staged so it cannot race.

    A blocker holds the write lock, so the backend's INSERT is provably still
    parked inside sqlite when its loop goes away — no sleeping, no hoping: the
    worker having drained the request queue is the signal that it picked the
    statement up, and the blocker is what guarantees it cannot get past it.
    Releasing the blocker afterwards makes the worker deliver a result to a
    loop that no longer exists, which is the exact moment the thread used to
    die.
    """
    db_path = str(tmp_path / "abandoned.db")

    setup = sqlite3.connect(db_path, isolation_level=None)
    setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    setup.close()

    # Open the backend BEFORE anyone holds the lock: `initialize()` runs
    # `PRAGMA journal_mode=WAL`, which wants an exclusive lock and fails fast
    # rather than honouring busy_timeout.
    backend = SQLiteBackend(db_path)
    asyncio.run(backend.initialize())

    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # holds the write lock

    async def _a_loop_that_dies_mid_write() -> None:
        task = asyncio.create_task(
            backend.execute_write("INSERT INTO t (v) VALUES (?)", ("abandoned",))
        )
        conn = backend._ensure_conn()
        await asyncio.sleep(0)  # let the task hand its statement to the worker
        while not conn._tx.empty():  # ...and let the worker pick it up
            await asyncio.sleep(0)
        # Walk away exactly like a portal loop shutting down after its
        # response: asyncio.run() cancels what is left and closes the loop.
        task.cancel()

    conn_before = None
    asyncio.run(_a_loop_that_dies_mid_write())
    conn_before = backend._ensure_conn()
    worker = conn_before._thread.name

    # The loop is gone; now let the parked statement complete and be delivered
    # to it. Unfixed, this is where the worker thread dies.
    blocker.rollback()
    blocker.close()

    async def _still_serving() -> None:
        # A dead worker can never answer this; a live one answers immediately.
        await asyncio.wait_for(conn_before.execute("SELECT 1"), timeout=5)

    try:
        asyncio.run(_still_serving())
    except asyncio.TimeoutError:  # pragma: no cover — the regression path
        pytest.fail(
            "the aiosqlite worker thread died delivering a result to a closed "
            "loop; its sqlite3 connection can never be closed now, and holds "
            "its lock for the life of the process"
        )

    assert worker in _live_threads()

    # The real payoff: the connection is still reclaimable, so the lock goes.
    asyncio.run(backend.close())
    assert worker not in _live_threads()

    with sqlite3.connect(db_path, timeout=5) as after:
        after.execute("INSERT INTO t (v) VALUES ('after')")


@pytest.mark.asyncio
async def test_evicting_a_closed_loops_client_closes_its_connection(monkeypatch, tmp_path):
    """`db_factory` must release what it evicts.

    Popping the dict entry only makes the client unreachable; the connection,
    its file handle and its lock stay exactly where they were. The registry is
    the last reference, so whatever it drops without closing is orphaned by
    definition.
    """
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(
        settings, "database_url", f"sqlite:///{tmp_path / 'evict.db'}"
    )
    monkeypatch.delenv("SQLITE_PROXY_URL", raising=False)

    holder: dict = {}

    async def _acquire_then_die() -> None:
        holder["client"] = await db_factory.get_db_client()
        holder["loop_id"] = id(asyncio.get_running_loop())

    # A whole loop's lifetime, ending with the client still registered.
    thread = threading.Thread(target=lambda: asyncio.run(_acquire_then_die()))
    thread.start()
    thread.join()

    client = holder["client"]
    worker = client._backend._ensure_conn()._thread.name
    assert holder["loop_id"] in db_factory._clients_by_loop
    assert worker in _live_threads()

    # Any acquisition on a live loop sweeps the dead one.
    await db_factory.get_db_client()

    assert holder["loop_id"] not in db_factory._clients_by_loop
    assert worker not in _live_threads(), (
        "the evicted client's connection is still open — db_factory forgot it "
        "instead of closing it"
    )

    await db_factory.close_db_client()
