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


def test_default_stays_owner_relay_for_existing_callers():
    """Signature back-compat: the team branch and any other caller that does
    not pass the flag must keep the pre-existing behaviour."""
    t = _trigger([])
    prompt = t._build_prompt([_msg("m4", "agent_x")], owner_user_id="user_tc")
    assert "## Owner Relay — REQUIRED" in prompt


# ---------------------------------------------------------------------------
# Detecting who started the thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_prior_message_from_us_means_we_are_being_asked():
    t = _trigger(rows=[])
    started = await t._agent_spoke_in_channel_before(
        "agent_yushu", "ch_dm_1", [_msg("m1", "agent_xiaoque")]
    )
    assert started is False


@pytest.mark.asyncio
async def test_a_prior_message_from_us_means_we_started_it():
    t = _trigger(rows=[{"message_id": "older_one"}])
    started = await t._agent_spoke_in_channel_before(
        "agent_xiaoque", "ch_dm_1", [_msg("m9", "agent_yushu")]
    )
    assert started is True


@pytest.mark.asyncio
async def test_the_incoming_batch_itself_does_not_count_as_prior():
    """Our own message could be in the batch (a channel we just posted to);
    counting it would flip every turn to Owner Relay and re-break the fix."""
    t = _trigger(rows=[{"message_id": "m_self"}])
    started = await t._agent_spoke_in_channel_before(
        "agent_yushu", "ch_dm_1", [_msg("m_self", "agent_yushu")]
    )
    assert started is False


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
    assert await t._agent_spoke_in_channel_before("a", "ch", [_msg("m", "b")]) is True
