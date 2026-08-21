"""
@file_name: test_nexus_steer_pump.py
@author: Bin Liang
@date: 2026-08-21
@description: NexusAgent._pump_steer_to_stdin — the subprocess steer
transport's write side: a push onto the run's SteerChannel becomes a
{"steer": …} line on the runner's stdin. (The runner-side read is covered
by test_runner_steer; the full loop delivery by the in-process e2e.)
"""

import asyncio
import json

import pytest

from xyz_agent_context.agent_framework.adapters.nexus.nexus_agent import NexusAgent
from xyz_agent_context.agent_runtime.steer_channel import SteerChannel, render_injection
from xyz_agent_context.schema.steer_schema import SteerInjection


class _FakeStdin:
    def __init__(self):
        self.lines: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.lines.append(data)

    async def drain(self) -> None:
        return None


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()


class _NeverCancel:
    def requested(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_push_becomes_a_steer_line_on_stdin():
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    channel = SteerChannel()
    inj = SteerInjection(run_id="r1", msg_id="m1", role="user",
                         content="reconsider", sender_id="bob", source="team")

    pump = asyncio.create_task(
        agent._pump_steer_to_stdin(proc, channel, _NeverCancel())
    )
    await channel.push(inj)
    # give the pump a couple of poll ticks to drain and write
    for _ in range(20):
        if proc.stdin.lines:
            break
        await asyncio.sleep(0.05)
    pump.cancel()

    assert proc.stdin.lines, "the pump must write the pushed injection to stdin"
    frame = json.loads(proc.stdin.lines[0].decode("utf-8"))
    assert frame["steer"] == render_injection(inj)
