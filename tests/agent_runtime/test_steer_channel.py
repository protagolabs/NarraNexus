"""
@file_name: test_steer_channel.py
@author: Bin Liang
@date: 2026-08-21
@description: SteerChannel — the push handle the orchestrator holds for a
live run. It renders a SteerInjection to a provider message and enqueues
it; in-process the loop's QueueSteeringInlet drains the same queue, so a
push shows up in the run's next step with no cross-process hop.
"""

import asyncio

import pytest

from xyz_agent_context.agent_runtime.steer_channel import SteerChannel, render_injection
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.steering import (
    QueueSteeringInlet,
)
from xyz_agent_context.schema.steer_schema import SteerInjection


def _inj(content: str, source: str = "team", sender: str = "agent_x") -> SteerInjection:
    return SteerInjection(
        run_id="r1", msg_id="m1", role="user", content=content,
        sender_id=sender, source=source,
    )


def test_render_tags_by_source_and_keeps_content():
    team = render_injection(_inj("hi team", source="team", sender="Bob"))
    assert team["role"] == "user"
    assert "hi team" in team["content"]
    assert "Bob" in team["content"]  # a teammate is named

    owner = render_injection(_inj("hi", source="owner_chat"))
    # The owner interjecting reads differently from a teammate — different tag.
    assert owner["content"] != team["content"].replace("hi team", "hi")


@pytest.mark.asyncio
async def test_push_lands_on_the_shared_queue_the_inlet_drains():
    chan = SteerChannel()
    inlet = QueueSteeringInlet(chan.queue)  # in-process: same queue

    await chan.push(_inj("first"))
    await chan.push(_inj("second"))

    drained = await inlet.drain()
    assert [m["content"] for m in drained] == [
        render_injection(_inj("first"))["content"],
        render_injection(_inj("second"))["content"],
    ]


@pytest.mark.asyncio
async def test_drain_pending_snapshots_and_empties():
    chan = SteerChannel()
    await chan.push(_inj("a"))
    await chan.push(_inj("b"))

    pending = chan.drain_pending()
    assert len(pending) == 2
    assert chan.drain_pending() == []
