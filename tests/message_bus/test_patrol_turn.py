"""
@file_name: test_patrol_turn.py
@author:
@date: 2026-08-10
@description: The patrol turn itself — when it speaks, and when it stays quiet.

A patrol that narrated every sweep would be a second kind of noise in a room
the product already fought to keep quiet (the folded console, the lingering
activity bubble). So: silence is the normal outcome, and the cursor moves
either way.

Also pinned here: a patrol message is the PLATFORM speaking. It posts under
the room's own marker, not as the lead, which is what keeps it out of the
agent-hop count (owner decision 2026-08-07, option a).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
)
from xyz_agent_context.message_bus.patrol import PATROL_MSG_TYPE
from xyz_agent_context.repository.team_work_repository import TeamWorkItemRepository
from xyz_agent_context.utils.timezone import utc_now


CHANNEL = "ch_room"
TEAM = "t1"


async def _seed_room(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in (("agent_lead", "Ana"), ("agent_worker", "Bruno")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name,
                                   "created_by": "usr_1"})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Desk",
        "lead_agent_id": "agent_lead",
    })


def _trigger(db, reply: str):
    """A trigger whose runtime returns `reply` and records the prompt it got."""
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))
    seen: dict = {}

    async def _invoke(**kwargs):
        seen.update(kwargs)
        return (reply, "evt_patrol")

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t, seen


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _get_db
    )


@pytest.mark.asyncio
async def test_a_quiet_patrol_says_nothing_but_moves_the_cursor(db_client):
    """Nothing wrong → nothing posted. The sweep still counts as done.

    A patrol that announced "all good" every ten minutes would be exactly the
    standing noise the room's design keeps removing.
    """
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, _ = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    msgs = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert msgs == []
    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert team["last_patrol_at"]


@pytest.mark.asyncio
async def test_a_patrol_with_something_to_say_posts_it(db_client):
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, _ = _trigger(db_client, "@Bruno 那个 OCR 还在吗?")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    msgs = await db_client.get("bus_messages", {"channel_id": CHANNEL})
    assert len(msgs) == 1
    assert msgs[0]["msg_type"] == PATROL_MSG_TYPE
    # Posted by the ROOM, not by the lead — that is what keeps it out of the
    # agent-hop count.
    assert msgs[0]["from_agent"] == f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}"


@pytest.mark.asyncio
async def test_the_cursor_moves_even_when_the_turn_fails(db_client):
    """A crashed patrol still consumed its slot; re-running it immediately
    would turn one broken team into a hot loop."""
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    async def _boom(**kwargs):
        raise RuntimeError("provider exploded")

    trigger._invoke_runtime = _boom  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert team["last_patrol_at"]


@pytest.mark.asyncio
async def test_the_speech_cap_silences_a_looping_patrol(db_client):
    """Past the cap the sweep still happens — it just stops posting."""
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    from xyz_agent_context.message_bus.patrol import PATROL_SPEECH_MAX

    await db_client.update("teams", {"team_id": TEAM}, {
        "patrol_spoke_at": utc_now(), "patrol_spoke_count": PATROL_SPEECH_MAX,
    })
    trigger, _ = _trigger(db_client, "@Bruno 还在吗?")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert await db_client.get("bus_messages", {"channel_id": CHANNEL}) == []
    team = await db_client.get_one("teams", {"team_id": TEAM})
    assert team["last_patrol_at"]


@pytest.mark.asyncio
async def test_the_patrol_prompt_carries_the_board_and_the_stalled_facts(db_client):
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR the scans",
                           created_by="agent_lead", assignee_id="agent_worker")
    trigger, seen = _trigger(db_client, "")

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    prompt = seen["prompt"]
    assert "OCR the scans" in prompt
    # The stall is stated as a platform fact, and the lead is told what it may
    # do about it — chase, not re-assign (owner decision 2026-08-07).
    assert "stalled" in prompt.lower()
    assert "reassign" not in prompt.lower() or "do not reassign" in prompt.lower()


@pytest.mark.asyncio
async def test_a_patrol_message_does_not_raise_the_cascade_depth(db_client):
    """The exemption that makes patrol work at all.

    A dead flow IS a long unbroken run of agent messages, so depth sits at the
    cap; if patrol's own line counted, its chase @ would be stripped in exactly
    the situation it exists for.
    """
    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    for i in range(3):
        await bus.send_message(from_agent="agent_worker", to_channel=CHANNEL,
                               content=f"hop {i}")
    trigger = MessageBusTrigger(bus=bus)
    before = await trigger._team_cascade_depth(CHANNEL)

    await bus.send_message(
        from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}", to_channel=CHANNEL,
        content="@Bruno still there?", msg_type=PATROL_MSG_TYPE,
    )

    assert await trigger._team_cascade_depth(CHANNEL) == before


@pytest.mark.asyncio
async def test_a_user_message_still_resets_the_depth(db_client):
    """The exemption must not break the thing the counter is for."""
    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    await bus.send_message(from_agent="agent_worker", to_channel=CHANNEL, content="a")
    await bus.send_message(from_agent="usr_1", to_channel=CHANNEL, content="hi")
    trigger = MessageBusTrigger(bus=bus)

    assert await trigger._team_cascade_depth(CHANNEL) == 0


@pytest.mark.asyncio
async def test_patrol_lines_do_not_eat_the_depth_window(db_client):
    """Skipping patrol rows in Python was not enough — they still ate the window.

    The depth query is a fixed `LIMIT MAX_TEAM_AGENT_HOPS + 2`. A skipped row
    still consumed one of those slots, so with enough patrol lines interleaved
    the countable hops could never reach the cap, and the runaway-@ protection
    stopped applying — precisely in the rooms patrol frequents, since it only
    speaks where a chain is already looping.
    """
    await _seed_room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    # Interleave so the newest 6 rows hold 3 patrol lines + 3 agent messages,
    # while 4 real hops exist just outside that window.
    for i in range(4):
        await bus.send_message(from_agent="agent_worker", to_channel=CHANNEL,
                               content=f"hop{i}")
    for i in range(3):
        await bus.send_message(
            from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}", to_channel=CHANNEL,
            content=f"sweep{i}", msg_type=PATROL_MSG_TYPE,
        )
    trigger = MessageBusTrigger(bus=bus)

    from xyz_agent_context.message_bus.message_bus_trigger import MAX_TEAM_AGENT_HOPS

    # All four agent hops must still be visible, so the cap can fire.
    assert await trigger._team_cascade_depth(CHANNEL) >= MAX_TEAM_AGENT_HOPS


@pytest.mark.asyncio
async def test_a_capped_patrol_does_not_run_the_turn_at_all(db_client):
    """The cap gates the TURN, not just the message.

    Checking only at post time still ran a full LLM turn and threw the output
    away: with a stalled board the pace is 180s against a cap of 6 per 30
    minutes, so roughly four entire runs per window were burned for nothing —
    right next to the "empty board, zero runs" guarantee this feature makes.
    """
    await _seed_room(db_client)
    repo = TeamWorkItemRepository(db_client)
    await repo.create_item(team_id=TEAM, channel_id=CHANNEL, title="OCR",
                           created_by="agent_lead", assignee_id="agent_worker")
    from xyz_agent_context.message_bus.patrol import PATROL_SPEECH_MAX

    await db_client.update("teams", {"team_id": TEAM}, {
        "patrol_spoke_at": utc_now(), "patrol_spoke_count": PATROL_SPEECH_MAX,
    })
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    ran = {"count": 0}

    async def _invoke(**kwargs):
        ran["count"] += 1
        return ("@Bruno 还在吗?", "evt_x")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._run_patrol(TEAM, "agent_lead", CHANNEL)

    assert ran["count"] == 0, "capped patrol must not spend an LLM turn"
    # The cursor still moves, or a capped team becomes a hot candidate.
    assert (await db_client.get_one("teams", {"team_id": TEAM}))["last_patrol_at"]
