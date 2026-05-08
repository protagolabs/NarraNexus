"""
@file_name: test_artifact_events.py
@author: Bin Liang
@date: 2026-05-08
@description: Unit tests for ArtifactEventBus — per-agent in-process pub/sub primitive.
"""

import asyncio

import pytest

from xyz_agent_context.utils.artifact_events import ArtifactEventBus


@pytest.mark.asyncio
async def test_subscriber_receives_published_event():
    bus = ArtifactEventBus()
    queue = bus.subscribe("agent_x")
    try:
        await bus.publish("agent_x", {"type": "artifact.created", "artifact_id": "art_1"})
        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert evt == {"type": "artifact.created", "artifact_id": "art_1"}
    finally:
        bus.unsubscribe("agent_x", queue)


@pytest.mark.asyncio
async def test_subscribers_for_other_agents_do_not_receive():
    bus = ArtifactEventBus()
    q_x = bus.subscribe("agent_x")
    q_y = bus.subscribe("agent_y")
    try:
        await bus.publish("agent_x", {"type": "artifact.created", "artifact_id": "art_1"})
        evt_x = await asyncio.wait_for(q_x.get(), timeout=1.0)
        assert evt_x["artifact_id"] == "art_1"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q_y.get(), timeout=0.1)
    finally:
        bus.unsubscribe("agent_x", q_x)
        bus.unsubscribe("agent_y", q_y)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = ArtifactEventBus()
    q = bus.subscribe("agent_x")
    bus.unsubscribe("agent_x", q)
    await bus.publish("agent_x", {"type": "artifact.deleted", "artifact_id": "art_1"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.1)
