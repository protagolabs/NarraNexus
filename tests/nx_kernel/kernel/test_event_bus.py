"""
@file_name: test_event_bus.py
@author: Bin Liang
@date: 2026-09-03
@description: Event bus — vocabulary gate, isolated handlers, timeouts, disposal, blocking.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.contracts import RegistryConflict, UnknownEntry
from narranexus.contracts.events import HOST_EVENTS
from narranexus.kernel.events.bus import EventBus


async def test_sync_and_async_handlers_receive_the_payload():
    bus = EventBus()
    got: list[str] = []

    async def async_handler(payload):
        got.append("async:" + payload["run_id"])

    bus.subscribe("onDidStartRun", lambda p: got.append("sync:" + p["run_id"]), owner="a")
    bus.subscribe("onDidStartRun", async_handler, owner="b")
    report = await bus.emit("onDidStartRun", {"run_id": "r1"})
    assert sorted(got) == ["async:r1", "sync:r1"]
    assert report.delivered == 2 and report.failed == [] and report.timed_out == []


async def test_unknown_event_is_rejected_for_subscribe_and_emit_and_declare_adds_it():
    bus = EventBus()
    with pytest.raises(UnknownEntry, match="not declared"):
        bus.subscribe("onDidTypo", lambda p: None, owner="a")
    with pytest.raises(UnknownEntry):
        await bus.emit("onDidTypo", {})
    bus.declare("onDidCustom")
    with pytest.raises(RegistryConflict):
        bus.declare("onDidCustom")
    bus.subscribe("onDidCustom", lambda p: None, owner="a")
    assert (await bus.emit("onDidCustom", {})).delivered == 1
    assert set(HOST_EVENTS) <= set(bus.names())


async def test_failing_handler_is_isolated_and_counted_per_owner():
    bus = EventBus()
    calls: list[str] = []

    def boom(payload):
        raise RuntimeError("bad plugin")

    bus.subscribe("onDidPersistTurn", boom, owner="broken")
    bus.subscribe("onDidPersistTurn", lambda p: calls.append("ok"), owner="fine")
    report = await bus.emit("onDidPersistTurn", {})
    assert calls == ["ok"] and report.delivered == 1
    assert [(o, type(e)) for o, e in report.failed] == [("broken", RuntimeError)]
    assert bus.error_counts["broken"] == 1 and bus.error_counts["fine"] == 0


async def test_slow_handler_is_cancelled_and_counted_as_slow():
    bus = EventBus(timeout_s=0.02)

    async def slow(payload):
        await asyncio.sleep(1)

    bus.subscribe("onDidCompleteRun", slow, owner="slowpoke")
    bus.subscribe("onDidCompleteRun", lambda p: None, owner="quick")
    report = await bus.emit("onDidCompleteRun", {})
    assert report.timed_out == ["slowpoke"] and report.delivered == 1
    assert bus.slow_counts["slowpoke"] == 1


async def test_dispose_and_block_stop_delivery():
    bus = EventBus()
    calls: list[str] = []
    d = bus.subscribe("onDidCancelRun", lambda p: calls.append("a"), owner="a")
    bus.subscribe("onDidCancelRun", lambda p: calls.append("b1"), owner="b")
    bus.subscribe("onDidChangeArtifact", lambda p: calls.append("b2"), owner="b")
    d.dispose()
    d.dispose()
    assert bus.block("b") == 2
    assert bus.subscriber_count("onDidCancelRun") == 0
    await bus.emit("onDidCancelRun", {})
    await bus.emit("onDidChangeArtifact", {})
    assert calls == []
