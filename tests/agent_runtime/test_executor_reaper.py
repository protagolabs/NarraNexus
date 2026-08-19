"""
@file_name: test_executor_reaper.py
@date: 2026-06-17
@description: Idle-cull reaper — stops idle executors, skips failures,
no-op without a broker. Pure coordinator, tested via DI fakes.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.executor_reaper import (
    ExecutorReaper,
    maybe_start_executor_reaper,
)


class _FakeController:
    """Returns a fixed idle set once, then empty (mimics claim semantics).

    Applies the reaper's ``is_busy`` veto the way the real controller does,
    so a vetoed user is neither returned nor consumed.
    """

    def __init__(self, idle):
        self._idle = list(idle)

    async def claim_idle_users(self, ttl_seconds, is_busy=None):
        keep, users = [], []
        for u in self._idle:
            if is_busy is not None and await is_busy(u):
                keep.append(u)
            else:
                users.append(u)
        self._idle = keep
        return users


@pytest.mark.asyncio
async def test_reap_once_stops_all_idle_users():
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    reaper = ExecutorReaper(_FakeController(["a", "b"]), stop_fn, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "b"]
    assert stopped == ["a", "b"]


@pytest.mark.asyncio
async def test_reap_once_skips_stop_failures():
    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")

    reaper = ExecutorReaper(_FakeController(["a", "b", "c"]), stop_fn, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "c"]   # b failed → skipped, pass not aborted


@pytest.mark.asyncio
async def test_reap_once_empty_when_nothing_idle():
    reaper = ExecutorReaper(_FakeController([]), lambda u: None, ttl_seconds=1)
    assert await reaper.reap_once() == []


@pytest.mark.asyncio
async def test_a_stop_failure_does_not_abort_the_pass():
    """One user's broker hiccup must not stop the others from being culled —
    the broker's own label-based reaper is the backstop for the one that
    failed."""
    stopped = []

    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")
        stopped.append(user_id)

    reaper = ExecutorReaper(
        _FakeController(["a", "b", "c"]), stop_fn, ttl_seconds=1
    )
    reaped = await reaper.reap_once()

    assert stopped == ["a", "c"]
    assert reaped == ["a", "c"]



@pytest.mark.asyncio
async def test_user_busy_in_another_process_is_never_stopped():
    """The 2026-07-31 regression: backend's reaper stopped the container out
    from under a group-chat run that was alive in the workers process."""
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    async def is_busy(user_id):
        return user_id == "group_chat_user"

    reaper = ExecutorReaper(
        _FakeController(["group_chat_user", "truly_idle"]),
        stop_fn,
        ttl_seconds=1,
        is_busy=is_busy,
    )
    assert await reaper.reap_once() == ["truly_idle"]
    assert stopped == ["truly_idle"]


@pytest.mark.asyncio
async def test_cross_process_check_says_busy_when_db_is_unreachable(monkeypatch):
    """No verdict must never authorise a cull (rule #14)."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime.executor_reaper import (
        cross_process_busy_check,
    )

    async def _boom():
        raise RuntimeError("no pool")

    monkeypatch.setattr(db_factory, "get_db_client", _boom)
    assert await cross_process_busy_check("u") is True


@pytest.mark.asyncio
async def test_cross_process_check_reads_live_runs_and_audits_the_skip(
    monkeypatch, db_client,
):
    """End-to-end over a real DB: a live run vetoes the cull and leaves an
    audit row; a finished one does not."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime.executor_reaper import (
        cross_process_busy_check,
    )
    from xyz_agent_context.schema.executor_audit import EVENT_CULL_SKIPPED_BUSY
    from xyz_agent_context.utils.timezone import utc_now

    async def _client():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _client)

    base = {
        "trigger": "message_bus",
        "trigger_source": "team_room",
        "agent_id": "agent_x",
        "created_at": "2026-07-31T00:00:00",
        "updated_at": "2026-07-31T00:00:00",
    }
    await db_client.insert("events", {
        **base, "event_id": "evt_live", "user_id": "busy_user",
        "state": "running", "started_at": utc_now(), "last_event_at": utc_now(),
    })
    await db_client.insert("events", {
        **base, "event_id": "evt_done", "user_id": "idle_user",
        "state": "completed", "started_at": utc_now(), "last_event_at": utc_now(),
    })

    assert await cross_process_busy_check("busy_user") is True
    assert await cross_process_busy_check("idle_user") is False

    rows = await db_client.get(
        "instance_executor_audit", {"event_type": EVENT_CULL_SKIPPED_BUSY}
    )
    assert [r["user_id"] for r in rows] == ["busy_user"]


def test_maybe_start_is_noop_without_broker(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert maybe_start_executor_reaper() is None
