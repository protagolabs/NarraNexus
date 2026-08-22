"""
@file_name: test_nexus_inprocess_steering.py
@author: Bin Liang
@date: 2026-08-21
@description: End-to-end through the NexusAgent driver (in-process path):
a message pushed onto a run's SteerChannel is drained into the loop at the
step boundary and rides the NEXT model request — no orchestrator, no
subprocess, real loop, fake model. Proves the driver actually mounts the
inlet over the channel's queue.
"""


import pytest

from xyz_agent_context.agent_framework.adapters.nexus.nexus_agent import NexusAgent
from xyz_agent_context.agent_framework.api_config import claude_config
from xyz_agent_context.agent_framework.nexus_power.contracts.events import Usage
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelEvent,
    ProviderProfile,
)
import re

from xyz_agent_context.agent_runtime.steer_channel import SteerChannel
from xyz_agent_context.schema.steer_schema import SteerInjection


class _FakeModel:
    """Two scripted steps: each is text + a non-tool end_turn. With a queued
    steering message the first step's boundary injects and forces the second."""

    def __init__(self, profile=None):
        self._steps = [
            [ModelEvent(kind="text_delta", payload={"text": "working"}),
             ModelEvent(kind="done", payload={"stop_reason": "end_turn",
                                              "usage": Usage(input_tokens=5, output_tokens=1)})],
            [ModelEvent(kind="text_delta", payload={"text": "reconsidered"}),
             ModelEvent(kind="done", payload={"stop_reason": "end_turn",
                                              "usage": Usage(input_tokens=5, output_tokens=1)})],
        ]
        self.profile = profile or ProviderProfile(name="fake", context_window=1000)
        self.requests = []

    def estimate_cost_usd(self, usage, model):
        return 0.0

    async def stream_step(self, request):
        self.requests.append(request)
        for ev in self._steps.pop(0):
            yield ev


@pytest.mark.asyncio
async def test_pushed_injection_reaches_the_next_model_request_in_process(monkeypatch):
    monkeypatch.setenv("NEXUS_POWER_INPROCESS", "1")
    monkeypatch.setattr(claude_config, "model", "fake-model")
    monkeypatch.setattr(claude_config, "api_key", "k")
    monkeypatch.setattr(claude_config, "base_url", "http://gw.local")
    monkeypatch.setattr(claude_config, "auth_type", "api_key")
    monkeypatch.setattr(claude_config, "thinking", "")

    fake = _FakeModel()
    import xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.model_client as mc_mod
    import xyz_agent_context.agent_framework.llm.litellm_client as lc_mod
    monkeypatch.setattr(mc_mod, "LiteLLMModelClient", lambda profile, client: fake)
    monkeypatch.setattr(lc_mod, "LitellmClient", lambda *a, **k: object())

    channel = SteerChannel()
    await channel.push(SteerInjection(
        run_id="r1", msg_id="m1", role="user",
        content="STEERED: reconsider", sender_id="teammate_bob", source="team",
    ))

    agent = NexusAgent(working_path="/tmp")
    events = [
        e async for e in agent.agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            mcp_servers={},
            steering=channel,
        )
    ]

    # The turn completed (a done frame was emitted) and the injection forced a
    # second model step whose request carries the rendered steer message.
    assert len(fake.requests) == 2
    injected = [
        m for m in fake.requests[1].messages
        if "STEERED: reconsider" in str(m.get("content", ""))
    ]
    assert injected, "the pushed steer message must ride the next model request"
    # render_injection stamps a random anti-forge nonce, so assert on structure
    # (not exact bytes of a second render): leading teammate tag, content
    # preserved byte-for-byte inside a matched-nonce block.
    content = injected[0]["content"]
    assert content.startswith("[teammate teammate_bob just posted to the room]")
    m = re.search(r"<message ([0-9a-f]{8})>\n(.*)\n</message \1>", content, re.DOTALL)
    assert m is not None, content
    assert m.group(2) == "STEERED: reconsider"
    assert events  # produced a legacy event stream
