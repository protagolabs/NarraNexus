"""
@file_name: test_cross_process_wake.py
@date: 2026-08-17
@description: A send from ANOTHER process wakes the poll loop too.

`_wake` (2026-08-14) closed the between-turns gap for posts made by the TRIGGER
process, and said so in its own docstring: an agent replying through an MCP tool
posts from the MCP server, where an in-process `asyncio.Event` cannot reach, so
that path still waited out the adaptive interval (3-12s). The mirror recorded the
remedy and deferred it — "要跨进程就得上 DB 信号 + 读取方,等 peer DM 延迟真成为抱怨
再说".

The harness redesign makes it a requirement rather than a nicety: a team reply
becomes a TOOL CALL (`message_team`), so the room's own relay moves onto exactly
the path the in-process Event never covered. Without a cross-process wake, moving
team delivery to a tool would hand back part of the latency win measured in
`c7739ad1` — a regression the user feels as the room going quiet.

So the signal lives at the ONE seam every send already passes through
(`LocalMessageBus.send_message` — the only `bus_messages` insert in the repo),
and the poll loop reads it while it sleeps. That placement also retires the
structural guard in `test_bus_relay_wake`: "post without a wake" stops being a
mistake a caller can make, because the wake is inside the write.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger

A, B = "agent_a", "agent_b"


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _get_db
    )


async def _seed_channel(db, channel="ch_x"):
    await db.insert("bus_channels", {
        "channel_id": channel, "name": "x", "channel_type": "group",
        "created_by": A,
    })
    for aid in (A, B):
        await db.insert("bus_channel_members", {"channel_id": channel, "agent_id": aid})
    return channel


# ── the signal ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_send_bumps_the_wake_signal(db_client):
    """Every send advances it — the seam is the write itself, not a caller."""
    from xyz_agent_context.message_bus import wake_signal

    ch = await _seed_channel(db_client)
    bus = LocalMessageBus(backend=db_client._backend)

    before = await wake_signal.read(db_client)
    await bus.send_message(from_agent=A, to_channel=ch, content="hi")
    after = await wake_signal.read(db_client)

    assert after != before


@pytest.mark.asyncio
async def test_the_signal_survives_a_missing_row(db_client):
    """First read on a fresh database must not raise — it just has no news."""
    from xyz_agent_context.message_bus import wake_signal

    assert await wake_signal.read(db_client) is not None


# ── the reader ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_sleep_returns_early_when_another_process_sends(db_client):
    """The point of the whole change.

    The trigger is sleeping its full interval; a send happens somewhere else
    entirely (no in-process Event is set). The sleep must still end promptly.
    """
    ch = await _seed_channel(db_client)
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    t._current_interval = 30  # far longer than the test may take

    async def _send_from_elsewhere():
        await asyncio.sleep(0.05)
        # Deliberately a DIFFERENT bus object: this stands for the MCP server
        # process, which shares the database and nothing else.
        other = LocalMessageBus(backend=db_client._backend)
        await other.send_message(from_agent=B, to_channel=ch, content="from afar")

    started = time.monotonic()
    await asyncio.gather(t._sleep_until_due(), _send_from_elsewhere())
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"slept {elapsed:.1f}s — the cross-process wake did not fire"


@pytest.mark.asyncio
async def test_the_sleep_still_ends_on_stop(db_client):
    """Stop must not have been traded away for the new waiter."""
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    t._current_interval = 30

    async def _stop():
        await asyncio.sleep(0.05)
        t.stop()

    started = time.monotonic()
    await asyncio.gather(t._sleep_until_due(), _stop())
    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_a_broken_signal_read_does_not_break_the_loop(db_client, monkeypatch):
    """Fail OPEN, and fail QUIETLY-BUT-VISIBLY.

    An unreadable signal means "no news", so the loop falls back to its timer.
    Raising here would take the whole poll loop down over a latency
    optimisation — the tail wagging the dog.
    """
    from xyz_agent_context.message_bus import wake_signal

    async def _boom(*_a, **_k):
        raise RuntimeError("signal table gone")

    monkeypatch.setattr(wake_signal, "read", _boom)

    t = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    t._current_interval = 0.2

    started = time.monotonic()
    await t._sleep_until_due()          # must return, not raise
    assert time.monotonic() - started >= 0.15


@pytest.mark.asyncio
async def test_a_send_that_fails_does_not_bump(db_client, monkeypatch):
    """The signal means "there IS new work", so a failed insert must not set it."""
    from xyz_agent_context.message_bus import wake_signal

    ch = await _seed_channel(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    before = await wake_signal.read(db_client)

    async def _boom(*_a, **_k):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(bus._db, "insert", _boom)
    with pytest.raises(RuntimeError):
        await bus.send_message(from_agent=A, to_channel=ch, content="nope")

    assert await wake_signal.read(db_client) == before
