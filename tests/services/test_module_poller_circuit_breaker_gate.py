"""
@file_name: test_module_poller_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: ModulePoller._execute_callback circuit-breaker skip-gate.

This Path-A trigger is currently dormant (Path B / JobTrigger is active), so
the gate is defensive. The test locks the wiring: a paused agent short-circuits
_execute_callback before it constructs an AgentRuntime.
"""
import pytest

import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.services.module_poller import ModulePoller


@pytest.mark.asyncio
async def test_execute_callback_skips_paused_agent(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return (True, "paused:auth")
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    # If the gate fails to short-circuit, this import target would blow up the
    # dormant path; guard it so a regression (gate removed) is a hard failure.
    def _boom(*a, **k):
        raise AssertionError("AgentRuntime must NOT be constructed for a paused agent")
    monkeypatch.setattr("narranexus.platform.agent_runtime.AgentRuntime", _boom, raising=False)

    poller = ModulePoller.__new__(ModulePoller)
    # Should return cleanly without constructing a runtime.
    await poller._execute_callback(
        agent_id="ag_paused",
        user_id="u",
        narrative_id="n",
        instance_id="i",
        trigger_data={},
    )


@pytest.mark.asyncio
async def test_execute_callback_claims_the_probe_before_building_a_runtime(monkeypatch):
    """The read gate passes (PAUSED, window open) but the probe claim is
    refused: no AgentRuntime. When the claim is granted, the runtime runs."""
    async def fake_skip(agent_id, db=None):
        return (False, None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    outcome = {"allowed": False}
    probe_calls = []

    async def fake_probe(agent_id, db=None):
        probe_calls.append(agent_id)
        return (True, None) if outcome["allowed"] else (False, "probing")
    monkeypatch.setattr(cb, "try_begin_probe", fake_probe)

    built = []

    class FakeRuntime:
        def __init__(self):
            built.append(self)

        async def _execute_callback_instance(self, **kw):
            pass
    monkeypatch.setattr("narranexus.platform.agent_runtime.AgentRuntime", FakeRuntime, raising=False)

    poller = ModulePoller.__new__(ModulePoller)
    kwargs = dict(agent_id="ag_paused", user_id="u", narrative_id="n",
                  instance_id="i", trigger_data={})
    await poller._execute_callback(**kwargs)
    assert probe_calls == ["ag_paused"]
    assert built == []  # refused → no runtime

    outcome["allowed"] = True
    await poller._execute_callback(**kwargs)
    assert len(built) == 1  # granted → the turn runs
