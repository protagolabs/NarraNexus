"""
@file_name: test_steer_routing.py
@author: Bin Liang
@date: 2026-08-21
@description: The orchestrator's steer decision — when a team lane is already
running, a new @mention is routed INTO the live run (steer_inbox + a push
onto its SteerChannel) and the processing cursor advances, instead of
waiting for the turn to end and dispatching a fresh one.

Uses the shared factory client (``get_db_client``) for the bus AND the
steer inbox, because that is what ``_route_steer`` reaches for — the two
must be one database, exactly as in production.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.agent_runtime.run_registry as run_registry
from xyz_agent_context.agent_runtime.run_registry import RunRegistry, get_run_registry
from xyz_agent_context.agent_runtime.steer_channel import SteerChannel
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.repository.steer_inbox_repository import SteerInboxRepository
from xyz_agent_context.utils.db.db_factory import get_db_client

ROOM = "ch_steer_route"
ME = "agent_steer_me"
RUN = "evt_steer_run1"


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


@pytest.mark.asyncio
async def test_route_steer_injects_into_live_run_and_advances_cursor():
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = SteerChannel()
    get_run_registry().register(ME, ROOM, RUN, channel)

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
    channel = SteerChannel()
    get_run_registry().register(ME, ROOM, RUN, channel)

    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="once", mentions=[ME],
    )
    await t._route_steer(ME, ROOM)
    await t._route_steer(ME, ROOM)  # second pass re-sees before ack in a race

    injs = await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
    assert len(injs) == 1  # (run_id, msg_id) unique → injected at most once
