"""
@file_name: test_steering_inlet.py
@author: Bin Liang
@date: 2026-08-21
@description: QueueSteeringInlet — the concrete step-boundary steering
inlet (the P4 TriggerInbox seam). Backs the SteeringInlet contract with
an in-process asyncio.Queue that the transport layer feeds; the loop
drains it at each step boundary. Unit behaviour only here; the
end-to-end "a queued message reaches the next model request" proof lives
in test_loop_e2e.py where the real loop harness already exists.
"""

import asyncio

import pytest

from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.steering import (
    NullSteeringInlet,
    QueueSteeringInlet,
)


@pytest.mark.asyncio
async def test_drain_returns_queued_messages_in_fifo_order_then_empties():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)
    await queue.put({"role": "user", "content": "first"})
    await queue.put({"role": "user", "content": "second"})

    drained = await inlet.drain()

    assert [m["content"] for m in drained] == ["first", "second"]
    # Draining consumes: a second drain with nothing added is empty.
    assert await inlet.drain() == []


@pytest.mark.asyncio
async def test_drain_on_empty_queue_returns_empty_list_without_blocking():
    inlet = QueueSteeringInlet(asyncio.Queue())
    # Must be non-blocking: the loop calls drain() every step boundary and
    # an empty inlet is the common case — it may never await a put().
    drained = await asyncio.wait_for(inlet.drain(), timeout=0.5)
    assert drained == []


@pytest.mark.asyncio
async def test_messages_put_between_drains_are_seen_by_the_later_drain():
    queue: asyncio.Queue = asyncio.Queue()
    inlet = QueueSteeringInlet(queue)

    assert await inlet.drain() == []
    await queue.put({"role": "user", "content": "arrived mid-turn"})
    drained = await inlet.drain()

    assert [m["content"] for m in drained] == ["arrived mid-turn"]


@pytest.mark.asyncio
async def test_satisfies_the_steering_inlet_contract_shape():
    # Same shape as the null inlet the contract test locks empty: one
    # coroutine `drain()` returning a list. Guards against the concrete
    # inlet drifting away from the protocol the loop depends on.
    inlet = QueueSteeringInlet(asyncio.Queue())
    assert hasattr(inlet, "drain")
    assert await inlet.drain() == await NullSteeringInlet().drain()
