"""
@file_name: test_unread_cursor.py
@author:
@date: 2026-08-11
@description: When a bus message stops counting as unread — and when it must not.

`bus_channel_members` carries two cursors and they answer different questions:
``last_processed_at`` is "the trigger drove this agent past here", ``last_read_at``
is "this agent has actually been shown it". Only the second one gates the unread
list that rides every turn's context.

Nothing advanced `last_read_at` in a team room. Its only writer keys off a bus
delivery tool appearing in the turn's trace, and a team reply is posted by the
trigger itself — server-side, no tool call — so the cursor sat at `joined_at`
forever. Every team message stayed unread for the rest of the agent's life, and
those messages ride EVERY scenario's context, owner chat included.

The fix is not "advance it wherever the trigger acks". Two of the four ack sites
run no turn at all, and the distinction is the whole point:

  * a turn ran → the room's scrollback was rendered into the prompt → the agent
    HAS seen those messages, whether or not it chose to reply;
  * no turn ran (not @mentioned, or rate-limited) → the agent has seen nothing.
    Marking those read would delete them unseen, and would take with them the
    one way an un-mentioned member ever learns what its room is up to.

Pinned here:
  * a team turn advances `last_read_at`, reply or silence
  * a stopped team turn advances it too — the prompt was still built
  * not being @mentioned does NOT advance it (the "glance at the room" path)
  * being rate-limited does NOT advance it
  * a peer DM keeps its selective mark_read semantics: no reply, still unread
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
)


CHANNEL = "ch_room"
TEAM = "t1"
ME = "agent_me"
PEER = "agent_peer"
USER = "usr_1"


async def _seed_team_room(db):
    """A two-agent team room, both members with a NULL read cursor."""
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in ((ME, "Mia"), (PEER, "Pat")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name,
                                   "created_by": "usr_1"})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Desk",
        "lead_agent_id": ME,
    })


async def _post(bus, *, mentions=None, sender=USER):
    return await bus.send_message(
        from_agent=sender, to_channel=CHANNEL, content="anyone home?",
        mentions=mentions,
    )


async def _read_cursor(db, agent_id=ME):
    row = await db.get_one(
        "bus_channel_members", {"channel_id": CHANNEL, "agent_id": agent_id}
    )
    return (row or {}).get("last_read_at")


def _trigger(db, reply: str = "on it"):
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))

    async def _invoke(**kwargs):
        return (reply, "evt_turn")

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _get_db
    )


# ── a turn ran → read ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_team_turn_marks_the_room_read(db_client):
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is not None


@pytest.mark.asyncio
async def test_a_silent_team_turn_marks_the_room_read_too(db_client):
    """Silence is a reply-discipline decision, not "I did not look".

    The scrollback was rendered into the prompt either way. Gating the cursor on
    "did it answer" is what DM channels do, and it is exactly the semantic that
    does not transfer: in a room, being shown a message IS the delivery.
    """
    trigger = _trigger(db_client, reply="")
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is not None


@pytest.mark.asyncio
async def test_a_stopped_team_turn_still_marks_the_room_read(db_client):
    """The owner pressing stop does not un-show what was already shown."""
    from xyz_agent_context.agent_runtime.cancellation import CancelledByUser

    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    async def _invoke(**kwargs):
        raise CancelledByUser("stopped by owner")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is not None


# ── no turn ran → NOT read ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unmentioned_member_keeps_the_room_unread(db_client):
    """The "glance at the room" path, and the reason this fix is not a one-liner.

    A member nobody @mentioned runs no turn and is shown nothing. The trigger
    still acks `last_processed_at` so it will not re-examine the batch — but
    marking it READ would drop the message unseen, and that unread entry is the
    only way this member ever finds out what its room has been doing.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[PEER])

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is None
    # …and the message is still on offer to the next turn, whatever wakes it.
    assert [m.content for m in await trigger._bus.get_unread(ME)] == ["anyone home?"]


@pytest.mark.asyncio
async def test_a_rate_limited_turn_keeps_the_room_unread(db_client):
    """Mentioned, but the platform declined to run it. Nothing was shown."""
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])
    trigger._check_rate_limit = lambda *a, **kw: False  # type: ignore[method-assign]

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is None


# ── DM keeps its own semantics ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_peer_dm_without_a_reply_stays_unread(db_client):
    """Reply Discipline lives on this cursor.

    In a DM the unread list IS the queue — "I will get to it" depends on the
    message resurfacing. Only an actual reply clears it, and that path runs
    through the module hook, not through here.
    """
    trigger = _trigger(db_client, reply="")
    await db_client.insert("bus_channels", {
        "channel_id": "ch_dm", "name": "dm", "channel_type": "direct",
        "created_by": PEER,
    })
    for aid in (ME, PEER):
        await db_client.insert(
            "bus_channel_members", {"channel_id": "ch_dm", "agent_id": aid}
        )
        await db_client.insert(
            "agents", {"agent_id": aid, "agent_name": aid, "created_by": "usr_1"}
        )
    await trigger._bus.send_message(
        from_agent=PEER, to_channel="ch_dm", content="ping",
    )

    await trigger._process_agent(ME)

    row = await db_client.get_one(
        "bus_channel_members", {"channel_id": "ch_dm", "agent_id": ME}
    )
    assert (row or {}).get("last_read_at") is None


# ── the cursor may not outrun what was rendered ─────────────────────────────

@pytest.mark.asyncio
async def test_a_backlog_deeper_than_the_scrollback_is_not_swallowed(db_client):
    """"A turn ran" is not "everything before it was shown".

    The prompt renders `TEAM_HISTORY_LIMIT` messages. Advancing the cursor to
    the trigger declares the WHOLE room read up to that point — so a member who
    accumulated 60 messages while nobody @mentioned it, then finally gets
    @mentioned, is shown 20 and silently loses 40 it never saw.

    That is the same failure this lane refuses to cause at the un-mentioned and
    rate-limited ack sites, arriving on the path that does run a turn. A single
    high-water cursor cannot say "read the window but not the gap below it", so
    when the rendered window does not reach back to the cursor, it must not move
    at all.
    """
    from xyz_agent_context.message_bus.message_bus_trigger import TEAM_HISTORY_LIMIT

    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    for i in range(TEAM_HISTORY_LIMIT + 5):
        await trigger._bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"backlog {i}",
        )
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is None
    # The un-rendered older messages are still on offer.
    unread = [m.content for m in await trigger._bus.get_unread(ME)]
    assert "backlog 0" in unread


@pytest.mark.asyncio
async def test_a_backlog_the_scrollback_covers_is_cleared(db_client):
    """The ordinary case, and the one that has to keep converging.

    When the window reaches back past the cursor there is no gap: everything
    between them was rendered, so the cursor advances and the room settles.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    for i in range(3):
        await trigger._bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"chat {i}",
        )
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_agent(ME)

    assert await _read_cursor(db_client) is not None
    assert await trigger._bus.get_unread(ME) == []
