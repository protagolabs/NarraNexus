"""
@file_name: test_undelivered_turn.py
@date: 2026-08-13
@description: A bus turn that delivered nothing must say so.

PRD 2026-08-04 「看到的必须是真的」§四. Three shapes of the same silence, all
of which used to end with an empty room and a user concluding the agent had
ignored them:

* team room — the turn produced no reply text;
* team room — the reply existed and the ROOM POST failed;
* A2A DM — the turn neither answered the peer nor said anything to its owner,
  leaving the asking agent blocked forever.

The regression guards matter as much as the new behaviour: a turn that DID
deliver must stay silent, or every ordinary reply grows a bogus "no reply"
line underneath it.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.delivery_notice import (
    DELIVERY_FAILED_MSG_TYPE,
    UNDELIVERED_MSG_TYPE,
)
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from xyz_agent_context.message_bus.schemas import BusMessage
from xyz_agent_context.schema.inbox_schema import InboxMessageType

ROOM = "ch_undelivered_room"
DM = "ch_undelivered_dm"


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


def _returns(monkeypatch, trigger, result: TurnResult):
    async def _fake(*_a, **_k):
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)


async def _notices(db_client, channel_id, msg_type):
    rows = await db_client.get("bus_messages", {"channel_id": channel_id})
    return [r for r in rows if r.get("msg_type") == msg_type]


# ── team room: the turn produced nothing ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_silent_team_turn_leaves_a_visible_line(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    notices = await _notices(db_client, ROOM, UNDELIVERED_MSG_TYPE)
    assert len(notices) == 1
    assert notices[0]["from_agent"] == "agent_a"
    # Nobody is blocked on a room silence; waking every member over it would
    # be worse than the silence.
    assert not notices[0]["mentions"]


@pytest.mark.asyncio
async def test_a_team_turn_that_replied_gets_no_notice(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="here you go", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert await _notices(db_client, ROOM, UNDELIVERED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_a_team_turn_that_posted_via_a_tool_gets_no_notice(
    db_client, monkeypatch
):
    """The team prompt forbids delivery tools, but a model that disobeys and
    posts through one HAS reached the room — claiming otherwise would be the
    same lie in the opposite direction."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(
        monkeypatch, trigger,
        TurnResult(text="", event_id="evt_1", delivered=True),
    )

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert await _notices(db_client, ROOM, UNDELIVERED_MSG_TYPE) == []


# ── team room: the post itself failed ───────────────────────────────────────


class _RoomPostFails(LocalMessageBus):
    """Fails the agent's reply post, lets platform notices through — the
    shape of a content-specific write failure (oversize row, encoding)."""

    async def send_message(self, *args, **kwargs):
        if kwargs.get("msg_type", "text") == "text":
            raise RuntimeError("room write rejected: sk-live-DEADBEEF0123456789")
        return await super().send_message(*args, **kwargs)


@pytest.mark.asyncio
async def test_a_failed_room_post_is_visible_and_redacted(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=_RoomPostFails(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="the answer", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    notices = await _notices(db_client, ROOM, DELIVERY_FAILED_MSG_TYPE)
    assert len(notices) == 1
    assert "sk-live-DEADBEEF0123456789" not in notices[0]["content"]


@pytest.mark.asyncio
async def test_a_failed_room_post_keeps_the_reply_in_the_owners_inbox(
    db_client, monkeypatch
):
    """The room line says it broke; the inbox keeps WHAT broke. A reply the
    owner paid for should not evaporate because one write failed."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=_RoomPostFails(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="the answer", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert any("the answer" in (r.get("content") or "") for r in rows)


# ── A2A: the peer that asked is the one left hanging ────────────────────────


@pytest.mark.asyncio
async def test_an_a2a_turn_that_reached_nobody_wakes_the_asker(
    db_client, monkeypatch
):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    notices = await _notices(db_client, DM, UNDELIVERED_MSG_TYPE)
    assert len(notices) == 1
    # The mention is the whole point: only a message wakes a blocked peer.
    assert "agent_b" in (notices[0]["mentions"] or "")


@pytest.mark.asyncio
async def test_an_a2a_silence_also_reaches_the_owner(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert any(
        r.get("message_type") == InboxMessageType.SYSTEM_NOTICE.value for r in rows
    )


@pytest.mark.asyncio
async def test_an_a2a_turn_that_answered_the_peer_gets_no_notice(
    db_client, monkeypatch
):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(
        monkeypatch, trigger,
        TurnResult(text="", event_id="evt_1", delivered=True),
    )

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_an_a2a_turn_that_relayed_to_its_owner_gets_no_notice(
    db_client, monkeypatch
):
    """Owner relay reached someone. Scope is deliberately "reached NOBODY":
    a turn that answered the wrong party is a routing problem, not silence,
    and dressing it as one would fire on every legitimate relay."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="told my owner", event_id="e"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_a_notice_never_answers_a_notice(db_client, monkeypatch):
    """Ping-pong guard. B goes silent → A is woken by the notice → if A also
    goes silent, a second notice would wake B, and two quiet agents would
    volley platform lines at each other forever."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b",
        content="This turn ended without delivering a reply.",
        msg_type=UNDELIVERED_MSG_TYPE,
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []
