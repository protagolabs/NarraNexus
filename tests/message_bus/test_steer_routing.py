"""
@file_name: test_steer_routing.py
@author: Bin Liang
@date: 2026-08-21
@description: The orchestrator's steer decision — when a team lane is already
running, a new @mention is routed INTO the live run (steer_inbox + a push
onto its SteerChannel) and the processing cursor advances, instead of
waiting for the turn to end and dispatching a fresh one.

Steering only ever carries messages NEWER than what the running turn already
rendered (its ``_InFlight.rendered_through`` high-water); the turn's own trigger
batch is already in its prompt and must not be re-injected. And the processing
cursor is FORWARD-ONLY, so the turn's own end-ack cannot pull it back behind the
messages steering already delivered.

Uses the shared factory client (``get_db_client``) for the bus AND the
steer inbox, because that is what ``_route_steer`` reaches for — the two
must be one database, exactly as in production.
"""
from __future__ import annotations

import asyncio

import pytest

import xyz_agent_context.agent_runtime.run_registry as run_registry
from xyz_agent_context.agent_runtime.run_registry import RunRegistry, get_run_registry
from xyz_agent_context.agent_runtime.steer_channel import SteerChannel
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger, _InFlight
from xyz_agent_context.repository.steer_inbox_repository import (
    SteerInboxFull,
    SteerInboxRepository,
)
from xyz_agent_context.utils.db.db_factory import get_db_client

ROOM = "ch_steer_route"
ME = "agent_steer_me"
RUN = "evt_steer_run1"

# A high-water BEFORE any message this test sends, so every sent message counts
# as "new" and is eligible to steer. Finding-A test overrides it to the future.
OLD_WATERMARK = "1970-01-01T00:00:00+00:00"


class _NullAudit:
    async def started(self, detail=None): ...
    async def stopped(self, detail=None): ...
    async def error(self, detail=None): ...
    async def heartbeat(self, detail=None, force=False): ...


async def _fresh_bus() -> LocalMessageBus:
    """A team room on the SHARED factory db, cleaned of any prior run's rows
    (the shared file db is session-scoped, so it can carry leftovers)."""
    db = await get_db_client()
    await db.execute("DELETE FROM bus_messages WHERE channel_id = %s", (ROOM,), fetch=False)
    await db.execute("DELETE FROM bus_channel_members WHERE channel_id = %s", (ROOM,), fetch=False)
    await db.execute("DELETE FROM bus_channels WHERE channel_id = %s", (ROOM,), fetch=False)
    await db.execute("DELETE FROM steer_inbox WHERE run_id = %s", (RUN,), fetch=False)
    # created_by = team_<id> marks a team room: delivery is pure @mention.
    await db.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    await db.insert("bus_channel_members", {"channel_id": ROOM, "agent_id": ME})
    return LocalMessageBus(db._backend)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(run_registry, "_registry", RunRegistry())


def _trigger(bus) -> MessageBusTrigger:
    t = MessageBusTrigger(bus=bus, max_workers=3)
    t.audit = _NullAudit()
    return t


async def _dummy_task() -> asyncio.Task:
    """A live task object for _InFlight — _route_steer only reads
    rendered_through off the flight, never the task, so any task will do."""
    task = asyncio.ensure_future(asyncio.sleep(3600))
    return task


async def _make_live_lane(t: MessageBusTrigger, *, watermark: str = OLD_WATERMARK):
    """Put the lane in the state a running steerable turn leaves it: a live run
    in the registry AND an in-flight flight carrying the rendered high-water."""
    channel = SteerChannel(agent_id=ME)
    channel.run_id = RUN
    get_run_registry().register(ME, ROOM, RUN, channel)
    t._in_flight[(ME, ROOM)] = _InFlight(
        task=await _dummy_task(), started_at=0.0, rendered_through=watermark
    )
    return channel


def _drain_flight(t: MessageBusTrigger) -> None:
    flight = t._in_flight.get((ME, ROOM))
    if flight is not None:
        flight.task.cancel()


@pytest.mark.asyncio
async def test_route_steer_injects_into_live_run_and_advances_cursor():
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="reconsider please",
            mentions=[ME],
        )

        await t._route_steer(ME, ROOM)

        injs = await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
        assert len(injs) == 1
        assert "reconsider please" in injs[0].content

        assert not channel.queue.empty()  # pushed onto the live run

        remaining = await bus.get_pending_messages(ME)
        assert [m for m in remaining if m.channel_id == ROOM] == []  # cursor advanced
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_route_steer_is_a_noop_when_the_run_is_not_registered():
    bus = await _fresh_bus()
    t = _trigger(bus)

    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="hi", mentions=[ME],
    )

    await t._route_steer(ME, ROOM)

    remaining = await bus.get_pending_messages(ME)
    assert [m for m in remaining if m.channel_id == ROOM]  # still pending
    assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []


@pytest.mark.asyncio
async def test_route_steer_dedups_across_cycles():
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="once", mentions=[ME],
        )
        await t._route_steer(ME, ROOM)
        await t._route_steer(ME, ROOM)  # second pass re-sees before ack in a race

        injs = await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
        assert len(injs) == 1  # (run_id, msg_id) unique → injected at most once
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_route_steer_does_not_reinject_the_turns_own_trigger_batch():
    # Finding A: while the turn runs, its trigger batch is unacked (the cursor
    # advances only at turn end), so get_pending_messages still returns it. A
    # steer that ignored the rendered high-water would re-inject the very batch
    # the turn is already acting on. With the watermark set AHEAD of the message
    # (as if the message were part of the rendered batch), it must be skipped.
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t, watermark="2099-12-31T23:59:59+00:00")
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="the trigger message",
            mentions=[ME],
        )

        await t._route_steer(ME, ROOM)

        # Not steered: nothing in the inbox, nothing pushed, and the cursor was
        # NOT advanced (the running turn owns this batch and will ack it itself).
        assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []
        assert channel.queue.empty()
        remaining = await bus.get_pending_messages(ME)
        assert [m for m in remaining if m.channel_id == ROOM]  # still pending
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_route_steer_advances_cursor_for_the_delivered_prefix_on_inbox_full(
    monkeypatch,
):
    # Finding B: if the run's steer_inbox is full partway through the batch, the
    # messages already delivered must still get the cursor advanced (so they do
    # not ALSO start a fresh turn); only the undelivered tail stays queued.
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="first", mentions=[ME],
        )
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="second", mentions=[ME],
        )

        real_append = SteerInboxRepository.append
        calls = {"n": 0}

        async def _append_then_full(self, inj):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise SteerInboxFull("run backlog at capacity")
            return await real_append(self, inj)

        monkeypatch.setattr(SteerInboxRepository, "append", _append_then_full)

        await t._route_steer(ME, ROOM)

        # The first was pushed and its cursor advanced; the second stays queued.
        assert channel.queue.qsize() == 1
        remaining = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
        assert len(remaining) == 1
        assert remaining[0].content == "second"
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_ack_processed_only_moves_forward():
    # The core of the steer cursor fix: a turn's own end-ack (to its older
    # trigger high-water) must not pull the cursor BACK behind messages that
    # steering already acked forward, or those resurface as a fresh turn.
    bus = await _fresh_bus()

    await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="A", mentions=[ME])
    await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="B", mentions=[ME])
    pend = sorted(
        (m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM),
        key=lambda m: str(m.created_at),
    )
    older, newer = pend[0], pend[1]

    await bus.ack_processed(ME, ROOM, newer.created_at)   # steering acked to B
    await bus.ack_processed(ME, ROOM, older.created_at)   # turn end-ack to A (older)

    # The cursor stayed at B — the backward ack was a no-op, nothing resurfaced.
    remaining = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
    assert remaining == []
