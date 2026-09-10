"""
@file_name: test_bus_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: MessageBusTrigger._process_lane circuit-breaker skip-gate.

A paused/cooling agent must be skipped WITHOUT consuming its pending bus
messages (they stay queued for when it resumes). The gate lives in the
production per-lane method and fires before the bus read.

Half-open probe (GitHub #117 review): ``should_skip`` is a pure read; the
single probe grant is claimed by ``try_begin_probe`` only at the point a
turn is actually about to run — AFTER the @mention filter and rate-limit
ack-and-return branches — so the 3s poller cannot burn the grant on a batch
it never runs. A refused claim leaves the batch queued.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


class _SpyBus:
    """Records whether the trigger tried to read pending messages, serves a
    fixed batch, and records acks."""
    def __init__(self, messages=None):
        self.get_pending_called = False
        self.messages = messages or []
        self.acks = []

    async def get_pending_messages(self, agent_id, limit=50, channel_id=None):
        self.get_pending_called = True
        return list(self.messages)

    async def ack_processed(self, agent_id, channel_id, created_at):
        self.acks.append((agent_id, channel_id, created_at))


_SEQ = iter(range(1, 10_000))


def _msg(from_agent="peer", mentions=None, created_at=None, **parts):
    """A real BusMessage (the production type): the lane runs multipart
    assembly on the batch, which reads the part_* fields — a thinner double
    would hide exactly that step."""
    n = next(_SEQ)
    return BusMessage(
        message_id=f"m_{n}",
        channel_id="ch_room",
        from_agent=from_agent,
        content=f"hello {n}",
        mentions=mentions,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        **parts,
    )


def _trigger(bus, channel_type="group", channel_owner="owner"):
    t = MessageBusTrigger.__new__(MessageBusTrigger)
    t._semaphore = asyncio.Semaphore(10)
    t._lane_locks = {}
    # `_process_lane` marks its dispatch as slot-holding for the heartbeat;
    # called directly there is no dispatch, so an empty registry is the
    # accurate state, not a stub.
    t._in_flight = {}
    t._bus = bus
    t._rate_counters = {}
    t.batches = []

    async def _channel_info(channel_id):
        return (channel_type, channel_owner)

    async def _batch(agent_id, channel_id, messages, trigger_msg, channel_owner=""):
        t.batches.append((agent_id, channel_id, messages))

    t._get_channel_info = _channel_info
    t._handle_channel_batch = _batch
    return t


def _probe_spy(monkeypatch, allowed=True):
    """Stub the claim and count how often the lane tried to take it."""
    calls = []

    async def fake_probe(agent_id, db=None):
        calls.append(agent_id)
        return (True, None) if allowed else (False, "probing")
    monkeypatch.setattr(cb, "try_begin_probe", fake_probe)
    return calls


@pytest.mark.asyncio
async def test_paused_agent_skipped_without_touching_bus(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return (True, "paused:auth")
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    bus = _SpyBus()
    t = _trigger(bus)
    result = await t._process_lane("ag_paused", "ch_test")

    assert result is False
    assert bus.get_pending_called is False  # messages left queued


@pytest.mark.asyncio
async def test_healthy_agent_falls_through_to_bus(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return (False, None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    bus = _SpyBus()
    t = _trigger(bus)
    result = await t._process_lane("ag_ok", "ch_test")

    # No pending messages → returns False, but it DID consult the bus.
    assert result is False
    assert bus.get_pending_called is True


@pytest.mark.asyncio
async def test_mention_filtered_batch_does_not_claim_the_probe(monkeypatch):
    """Group room, the agent is not @mentioned: the batch is acked without a
    turn — and the probe grant must NOT have been taken on the way."""
    async def fake_skip(agent_id, db=None):
        return (False, None)  # PAUSED but window open: the read gate passes
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch)

    bus = _SpyBus([_msg(mentions=["someone_else"])])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is False

    assert bus.acks  # cursor advanced past the irrelevant batch
    assert probe_calls == []  # grant untouched
    assert t.batches == []


@pytest.mark.asyncio
async def test_refused_probe_leaves_relevant_batch_queued(monkeypatch):
    """A relevant batch whose claim loses the race is NOT run and NOT acked —
    it stays queued for the next poll, exactly like a should_skip skip."""
    async def fake_skip(agent_id, db=None):
        return (False, None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch, allowed=False)

    bus = _SpyBus([_msg(mentions=["ag_paused"])])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is False

    assert probe_calls == ["ag_paused"]
    assert bus.acks == []
    assert t.batches == []


@pytest.mark.asyncio
async def test_granted_probe_runs_the_relevant_batch(monkeypatch):
    """The allowed case: the claim is taken exactly once, right before the
    batch runs."""
    async def fake_skip(agent_id, db=None):
        return (False, None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch, allowed=True)

    bus = _SpyBus([_msg(mentions=["ag_paused"])])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is True

    assert probe_calls == ["ag_paused"]
    assert len(t.batches) == 1


@pytest.mark.asyncio
async def test_held_multipart_batch_does_not_claim_the_probe(monkeypatch):
    """A long message still arriving in parts holds the lane (no ack, no
    turn) — and the hold branch sits BEFORE the claim, so the poller does
    not burn the probe on a fragment it will not run."""
    async def fake_skip(agent_id, db=None):
        return (False, None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch)

    bus = _SpyBus([_msg(mentions=["ag_paused"], part_index=1, part_count=2, part_group="grp_1")])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is False

    assert bus.acks == []  # held, not acked
    assert probe_calls == []  # grant untouched
    assert t.batches == []
