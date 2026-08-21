"""
@file_name: test_agent_dm_inbox.py
@date: 2026-08-20
@description: An agent-to-agent DM has to reach the recipient's Agent Inbox.

The 2026-08-17 refactor re-pointed the inbox panel to `inbox_threads` /
`inbox_thread_messages` and rewired the IM triggers to write them, but the
agent-to-agent (peer DM) delivery path was never given a writer for those
tables. A2A messages landed only in `bus_messages` (+ the separate owner
`inbox_table` notification), so `GET /api/agent-inbox?agent_id=` found no thread
and the panel rendered empty every time — even after agents actually talked.

These tests drive a real peer-DM turn through the trigger and assert the
recipient's inbox thread + messages exist. Delete the recording hook and they
go red.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.channel.inbox_recorder import agent_dm_thread_id
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    MessageBusTrigger,
    TurnResult,
)

A, B = "agent_alice", "agent_bob"
USER = "usr_dm"


async def _seed_agents(db):
    for aid, name in ((A, "Alice"), (B, "Bob")):
        await db.insert(
            "agents",
            {"agent_id": aid, "agent_name": name, "created_by": USER},
        )


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _get_db
    )


def _trigger(db, reply_text: str) -> MessageBusTrigger:
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))

    async def _invoke(**kwargs):
        # A peer DM turn: the agent's reply is plain text (no team_room path).
        return TurnResult(text=reply_text, event_id="evt_bob", delivered=False)

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t


@pytest.mark.asyncio
async def test_peer_dm_lands_in_recipient_agent_inbox(db_client):
    await _seed_agents(db_client)
    trig = _trigger(db_client, "got it, Alice")

    # Alice DMs Bob; the trigger runs Bob's turn.
    await trig._bus.send_to_agent(
        from_agent=A, to_agent=B, content="hey Bob, can you help?"
    )
    await trig._process_agent(B)

    thread_id = agent_dm_thread_id(B, A)
    thread = await db_client.get_one("inbox_threads", {"thread_id": thread_id})
    assert thread is not None, (
        "Bob's Agent Inbox has no thread for the DM Alice just sent — the A2A "
        "path is not writing inbox_threads"
    )
    assert thread["agent_id"] == B
    assert thread["source"] == "agent_dm"
    assert thread["counterpart_id"] == A

    msgs = await db_client.get(
        "inbox_thread_messages", {"thread_id": thread_id}
    )
    contents = {m["direction"]: m["content"] for m in msgs}
    assert contents.get("in") == "hey Bob, can you help?", (
        "the inbound peer message is missing from Bob's inbox thread"
    )
    assert contents.get("out") == "got it, Alice", (
        "Bob's reply is missing from his inbox thread"
    )


@pytest.mark.asyncio
async def test_fatal_turn_does_not_record_the_failure_notice_as_a_reply(db_client):
    """On a fatal turn `turn.text` is the platform's error notice, not the
    agent's words — it must not land in the conversation as the agent's reply.
    The inbound is still recorded: the peer's message did arrive.
    """
    await _seed_agents(db_client)
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))

    async def _invoke(**kwargs):
        return TurnResult(
            text="⚠️ the agent hit an error and could not continue",
            event_id="evt_bob",
            delivered=False,
            fatal=True,
        )

    t._invoke_runtime = _invoke  # type: ignore[method-assign]

    await t._bus.send_to_agent(from_agent=A, to_agent=B, content="you there?")
    await t._process_agent(B)

    thread_id = agent_dm_thread_id(B, A)
    msgs = await db_client.get(
        "inbox_thread_messages", {"thread_id": thread_id}
    )
    dirs = {m["direction"] for m in msgs}
    assert "in" in dirs, "the peer's message must still be recorded"
    assert "out" not in dirs, (
        "a fatal turn's failure notice was recorded as the agent's reply"
    )


@pytest.mark.asyncio
async def test_silent_recipient_still_records_the_inbound(db_client):
    """Bob receives a DM but stays silent — the inbound must still show."""
    await _seed_agents(db_client)
    trig = _trigger(db_client, "")  # empty reply: silent turn

    await trig._bus.send_to_agent(
        from_agent=A, to_agent=B, content="ping"
    )
    await trig._process_agent(B)

    thread_id = agent_dm_thread_id(B, A)
    thread = await db_client.get_one("inbox_threads", {"thread_id": thread_id})
    assert thread is not None, "a silent turn dropped the inbound entirely"

    msgs = await db_client.get(
        "inbox_thread_messages", {"thread_id": thread_id}
    )
    dirs = {m["direction"] for m in msgs}
    assert "in" in dirs, "the peer's message was not recorded"
    assert "out" not in dirs, "a silent turn must not leave an empty reply bubble"
