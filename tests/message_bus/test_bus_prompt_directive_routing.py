"""
@file_name: test_bus_prompt_directive_routing.py
@author: NarraNexus
@date: 2026-08-01
@description: A bus-triggered turn must be told to answer the right party
(P1 2026-08-03, found by running the incident scenario live).

The bug: ``_build_prompt`` appended "## Owner Relay — REQUIRED" whenever the
agent had an owner — which is always. So an agent receiving a FRESH question
from a peer was told:

    "Your owner originally asked you to contact this peer agent.
     They are waiting in chat for the answer."

For the RECIPIENT that is simply false: its owner asked for nothing. Three
live runs confirmed the consequence — 羽书 called
``send_message_to_user_directly`` and reported the errand discharged
("未回复小雀 — 她是转发…按 Reply Discipline"), while 小雀, which had promised
its user a report, waited forever. The models were obeying the prompt; the
prompt was wrong.

So the directive is now selected by WHO STARTED the thread:
  - a reply to our own errand  → Owner Relay (unchanged)
  - a fresh inbound question   → answer the PEER on the bus
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage


def _msg(message_id: str, from_agent: str, content: str = "你在干嘛？") -> BusMessage:
    return BusMessage(
        message_id=message_id,
        channel_id="ch_dm_1",
        from_agent=from_agent,
        content=content,
        msg_type="text",
        mentions=[],
        created_at="2026-08-03T22:48:34+00:00",
    )


class _FakeDB:
    """Returns whatever prior-message rows the test wants."""

    placeholder = "?"

    def __init__(self, rows):
        self._rows = rows
        self.queries: list = []

    async def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self._rows


class _FakeBus:
    def __init__(self, rows):
        self._db = _FakeDB(rows)


def _trigger(rows):
    return MessageBusTrigger(bus=_FakeBus(rows))


# ---------------------------------------------------------------------------
# Which directive gets appended
# ---------------------------------------------------------------------------


def test_fresh_inbound_question_tells_the_agent_to_answer_the_peer():
    t = _trigger([])
    prompt = t._build_prompt(
        [_msg("m1", "agent_xiaoque")],
        owner_user_id="user_tc",
        owner_name="TC",
        i_started_this_exchange=False,
    )

    assert "## Answer the peer — REQUIRED" in prompt
    assert "Owner Relay" not in prompt
    # It must name the actual reply channel...
    assert "bus_send_to_agent" in prompt
    # ...and kill the two rationalisations seen live.
    assert "your owner is NOT waiting" in prompt
    assert "never a substitute for replying to the peer" in prompt
    assert "A question is never a ping-pong" in prompt


def test_reply_to_our_own_errand_still_gets_owner_relay():
    """The 2026-06 silent-failure fix must not regress."""
    t = _trigger([])
    prompt = t._build_prompt(
        [_msg("m2", "agent_yushu", "我在做 X")],
        owner_user_id="user_tc",
        owner_name="TC",
        i_started_this_exchange=True,
    )

    assert "## Owner Relay — REQUIRED" in prompt
    assert "Answer the peer" not in prompt
    assert "send_message_to_user_directly" in prompt


def test_no_owner_means_no_directive_either_way():
    t = _trigger([])
    for started in (True, False):
        prompt = t._build_prompt(
            [_msg("m3", "agent_x")], owner_user_id="", i_started_this_exchange=started
        )
        assert "REQUIRED" not in prompt


def test_the_directive_flag_is_required():
    """No default: the flag decides which of two contradictory directives the
    agent gets, so a caller that forgets it must fail loudly rather than
    silently inherit Owner Relay (the pre-fix, wrong-for-recipients branch)."""
    t = _trigger([])
    with pytest.raises(TypeError):
        t._build_prompt([_msg("m4", "agent_x")], owner_user_id="user_tc")


# ---------------------------------------------------------------------------
# Classifying the incoming batch (question to us vs reply to our errand)
# ---------------------------------------------------------------------------


def _msg_src(message_id, from_agent, source):
    m = _msg(message_id, from_agent)
    return m.model_copy(update={"sender_turn_source": source})


@pytest.mark.asyncio
async def test_owner_facing_send_means_we_are_being_asked():
    """The peer sent this from a chat turn → it is running an errand for its
    owner and asking US."""
    t = _trigger([])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu", "ch_dm_1", [_msg_src("m1", "agent_xiaoque", "chat")]
    ) is False


@pytest.mark.asyncio
async def test_bus_send_means_it_is_the_reply_to_our_errand():
    t = _trigger([])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_xiaoque", "ch_dm_1", [_msg_src("m2", "agent_yushu", "message_bus")]
    ) is True


@pytest.mark.asyncio
async def test_follow_up_question_still_reads_as_being_asked():
    """The regression the first heuristic had: after the recipient answers
    once, a follow-up must NOT flip it to Owner Relay."""
    t = _trigger(rows=[{"message_id": "our_earlier_reply"}])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu", "ch_dm_1", [_msg_src("m3", "agent_xiaoque", "chat")]
    ) is False


@pytest.mark.asyncio
async def test_reverse_direction_in_a_reused_dm_channel():
    """The review's finding: DM channels are found symmetrically and REUSED,
    so "who opened the channel" is fixed forever. Once A has DM'd B, an errand
    B later runs toward A must still classify correctly for BOTH sides —
    otherwise that direction is permanently broken.
    """
    t = _trigger(rows=[{"message_id": "as_old_message"}])
    # B asks A on B's own errand → A is being asked, even though A opened the channel.
    assert await t._incoming_is_reply_to_my_errand(
        "agent_a", "ch_dm_1", [_msg_src("q", "agent_b", "chat")]
    ) is False
    # A answers → B must relay to ITS owner (this is the step my first fix regressed).
    assert await t._incoming_is_reply_to_my_errand(
        "agent_b", "ch_dm_1", [_msg_src("a", "agent_a", "message_bus")]
    ) is True


@pytest.mark.asyncio
async def test_mixed_batch_with_any_owner_facing_send_is_a_question():
    t = _trigger([])
    batch = [
        _msg_src("m1", "agent_xiaoque", "message_bus"),
        _msg_src("m2", "agent_xiaoque", "chat"),
    ]
    assert await t._incoming_is_reply_to_my_errand("a", "ch", batch) is False


# --- degradation when the source was not recorded -------------------------


@pytest.mark.asyncio
async def test_unknown_source_and_we_never_spoke_here_means_asked():
    """Legacy rows / adapters that drop the header. Absence of our own prior
    message is still unambiguous."""
    t = _trigger(rows=[])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu", "ch_dm_1", [_msg("m1", "agent_xiaoque")]
    ) is False


@pytest.mark.asyncio
async def test_unknown_source_but_we_have_spoken_falls_back_to_owner_relay():
    t = _trigger(rows=[{"message_id": "older"}])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_xiaoque", "ch_dm_1", [_msg("m9", "agent_yushu")]
    ) is True


@pytest.mark.asyncio
async def test_db_failure_falls_back_to_owner_relay():
    """Fail toward the OLD behaviour: a wrongly-relayed answer is an
    annoyance, but wrongly suppressing Owner Relay resurrects the
    silent-failure this directive exists to prevent."""

    class _Boom:
        placeholder = "?"

        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    class _Bus:
        _db = _Boom()

    t = MessageBusTrigger(bus=_Bus())
    assert await t._incoming_is_reply_to_my_errand("a", "ch", [_msg("m", "b")]) is True


# ---------------------------------------------------------------------------
# The fact must actually persist and round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sender_turn_source_round_trips_through_the_db():
    """Mocks cannot prove the column exists, is written, and comes back on the
    BusMessage the trigger reads. Without all three the classifier silently
    degrades to the fallback everywhere."""
    from xyz_agent_context.message_bus.local_bus import LocalMessageBus
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    db = await AsyncDatabaseClient.create_with_backend(backend)
    try:
        for aid in ("agent_a", "agent_b"):
            await db.insert("agents", {
                "agent_id": aid, "agent_name": aid, "created_by": "user_tc",
            })
        bus = LocalMessageBus(backend=db._backend)

        await bus.send_to_agent(
            from_agent="agent_a", to_agent="agent_b",
            content="what are you working on?", sender_turn_source="chat",
        )
        pending = await bus.get_pending_messages("agent_b")
        assert pending, "recipient must see the message"
        assert pending[0].sender_turn_source == "chat"

        trigger = MessageBusTrigger(bus=bus)
        # An errand question → the recipient must be told to answer the peer.
        assert await trigger._incoming_is_reply_to_my_errand(
            "agent_b", pending[0].channel_id, pending
        ) is False
    finally:
        await db.close()
