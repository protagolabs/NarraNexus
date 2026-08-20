"""
@file_name: test_nexus_warmup.py
@date: 2026-08-20
@description: NexusAgent.warmup() eagerly fills the warm-runner pool at
executor startup so the process's FIRST nexus_power turn also gets a
pre-imported runner (measured ~12s cold vs ~2s warm on dev). No-op in
in-process mode or when pooling is disabled; best-effort (never raises).
"""
import pytest

from xyz_agent_context.agent_framework.adapters.nexus import nexus_agent as na


def _fake_pool(enabled, calls):
    class FakePool:
        pass

    FakePool.enabled = enabled
    FakePool.schedule_refill = lambda self: calls.__setitem__("refill", calls["refill"] + 1)
    return FakePool()


def test_warmup_schedules_pool_refill_in_subprocess_mode(monkeypatch):
    monkeypatch.delenv("NEXUS_POWER_INPROCESS", raising=False)
    calls = {"refill": 0}
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: _fake_pool(True, calls)))
    agent = na.NexusAgent()
    calls["refill"] = 0  # ignore any __init__-time refill
    agent.warmup()
    assert calls["refill"] == 1


def test_warmup_is_noop_in_inprocess_mode(monkeypatch):
    monkeypatch.setenv("NEXUS_POWER_INPROCESS", "1")
    calls = {"refill": 0}
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: _fake_pool(True, calls)))
    agent = na.NexusAgent()
    calls["refill"] = 0
    agent.warmup()
    assert calls["refill"] == 0


def test_warmup_is_noop_when_pool_disabled(monkeypatch):
    monkeypatch.delenv("NEXUS_POWER_INPROCESS", raising=False)
    calls = {"refill": 0}
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: _fake_pool(False, calls)))
    agent = na.NexusAgent()
    calls["refill"] = 0
    agent.warmup()
    assert calls["refill"] == 0
