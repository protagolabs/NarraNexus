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
import contextlib
import json

import pytest

from xyz_agent_context.agent_framework.adapters.nexus.nexus_agent import NexusAgent
from xyz_agent_context.agent_runtime.steer_channel import (
    SteerChannel,
    rendered_injection_payload,
)
from xyz_agent_context.schema.steer_schema import SteerInjection


class _FakeStdin:
    def __init__(self):
        self.lines: list[bytes] = []
        self.on_write = lambda: None
        self.closed = False

    def write(self, data: bytes) -> None:
        self.lines.append(data)
        self.on_write()

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


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

    written = asyncio.Event()
    proc.stdin.on_write = written.set  # fire when the pump writes a line
    pump = asyncio.create_task(
        agent._pump_steer_to_stdin(proc, channel, _NeverCancel())
    )
    try:
        await channel.push(inj)
        await asyncio.wait_for(written.wait(), timeout=2)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump

    assert proc.stdin.lines, "the pump must write the pushed injection to stdin"
    frame = json.loads(proc.stdin.lines[0].decode("utf-8"))
    # render_injection now stamps a random per-render nonce into the block
    # delimiter (anti-forge, see test_steer_channel), so it is no longer a pure
    # function — assert on structure, not on a second render's exact bytes: the
    # frame carries the SAME rendered dict the channel enqueued (role, leading
    # tag, and the pushed content preserved byte-for-byte inside its block).
    steer = frame["steer"]
    assert steer["role"] == "user"
    assert steer["content"].startswith("[teammate bob just posted to the room]")
    assert rendered_injection_payload(steer["content"]) == "reconsider"


@pytest.mark.asyncio
async def test_non_steerable_run_closes_stdin_after_the_request():
    # steer_channel=None is the default path: the request is written and stdin
    # is CLOSED at once (the runner's reader then EOFs) — the "zero behaviour
    # change" invariant the PR relies on.
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    pump = await agent._open_steer_transport(
        proc, {"thread_id": "t", "messages": [], "options": {}}, None, _NeverCancel()
    )
    assert pump is None
    assert proc.stdin.closed is True
    assert proc.stdin.lines  # the request line was written


@pytest.mark.asyncio
async def test_steerable_run_keeps_stdin_open_for_the_pump():
    # A steerable run must NOT close stdin after the request — the pump needs it
    # open to write steer lines for the life of the turn.
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    channel = SteerChannel()
    pump = await agent._open_steer_transport(
        proc, {"thread_id": "t", "messages": [], "options": {}}, channel, _NeverCancel()
    )
    try:
        assert pump is not None
        assert proc.stdin.closed is False  # kept open for the pump
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
