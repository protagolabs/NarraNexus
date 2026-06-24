"""
@file_name: test_team_room_routing.py
@author: NetMind.AI
@date: 2026-06-24
@description: MessageBusTrigger forces a dedicated team-room narrative for team
group-chat channels (so the reply never pollutes the agent's 1:1 narratives),
and leaves every other channel on normal 1:1 narrative selection.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage
from xyz_agent_context.narrative._narrative_impl.team_room import (
    build_team_room_narrative_id,
)


def _msg(channel_id: str, from_agent: str, content: str = "hi") -> BusMessage:
    return BusMessage(
        message_id="msg_1",
        channel_id=channel_id,
        from_agent=from_agent,
        content=content,
        created_at="2026-06-24T00:00:00+00:00",
    )


class _FakeDb:
    def __init__(self, agents):
        self._agents = agents  # agent_id -> row dict

    async def get_one(self, table, filters):
        if table == "agents":
            return self._agents.get(filters.get("agent_id"))
        return None


class _FakeBus:
    def __init__(self, agents):
        self._db = _FakeDb(agents)
        self.sent = []
        self.acked = []

    async def get_channel_members(self, channel_id):
        return [SimpleNamespace(agent_id=a) for a in self._db._agents]

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return "msg_reply"

    async def ack_processed(self, **kwargs):
        self.acked.append(kwargs)


# ---------------------------------------------------------------------------
# _invoke_runtime threads forced_narrative_id into collect_run only when set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_runtime_forwards_forced_narrative_id(monkeypatch):
    captured = {}

    class _DummyRuntime:
        pass

    async def _fake_collect_run(runtime, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(is_error=False, output_text="ok")

    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.AgentRuntime", _DummyRuntime, raising=False
    )
    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.run_collector.collect_run",
        _fake_collect_run,
    )

    trigger = MessageBusTrigger(bus=_FakeBus({}))
    out = await trigger._invoke_runtime(
        agent_id="agent_1",
        sender_agent_id="usr_owner",
        prompt="p",
        channel_id="chan_x",
        forced_narrative_id="nar_room_abc",
    )
    assert out == "ok"
    assert captured.get("forced_narrative_id") == "nar_room_abc"

    captured.clear()
    await trigger._invoke_runtime(
        agent_id="agent_1",
        sender_agent_id="usr_owner",
        prompt="p",
        channel_id="chan_x",
    )
    # No stray forced id leaks onto a normal (non-team) run.
    assert "forced_narrative_id" not in captured


# ---------------------------------------------------------------------------
# Team branch forces the deterministic room narrative; peer DM does not
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_branch_forces_room_narrative(monkeypatch, db_client):
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client",
        lambda: _async(db_client),
    )

    captured = {}

    async def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return "plain reply"

    bus = _FakeBus({"agent_1": {"agent_name": "Alice"}})
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _fake_invoke)

    channel_id = "chan_team_42"
    messages = [_msg(channel_id, "usr_owner", "hello team")]
    await trigger._handle_channel_batch(
        agent_id="agent_1",
        channel_id=channel_id,
        messages=messages,
        trigger_message=messages[-1],
        channel_owner="team_42",  # synthetic team-room marker
    )

    expected = build_team_room_narrative_id("agent_1", channel_id)
    assert captured.get("forced_narrative_id") == expected
    # The room narrative was actually persisted.
    rows = await db_client.get("narratives", filters={"narrative_id": expected})
    assert len(rows) == 1
    # Reply posted back into the room.
    assert bus.sent and bus.sent[-1]["to_channel"] == channel_id


@pytest.mark.asyncio
async def test_peer_dm_does_not_force_narrative(monkeypatch):
    captured = {}

    async def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return "reply"

    async def _noop(*a, **k):
        return None

    bus = _FakeBus({"agent_1": {"agent_name": "Alice", "created_by": "user_owner"}})
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _fake_invoke)
    monkeypatch.setattr(trigger, "_get_agent_owner", _noop)  # skip owner lookup
    monkeypatch.setattr(trigger, "_write_to_inbox", _noop)

    channel_id = "chan_dm"
    messages = [_msg(channel_id, "agent_2", "ping")]
    await trigger._handle_channel_batch(
        agent_id="agent_1",
        channel_id=channel_id,
        messages=messages,
        trigger_message=messages[-1],
        channel_owner="agent_1",  # peer DM / owner-relay, NOT a team marker
    )

    # Peer DM keeps normal 1:1 narrative selection — no forced id.
    assert captured.get("forced_narrative_id", "") == ""


def _async(value):
    async def _coro():
        return value

    return _coro()
