"""
@file_name: test_driver_contract.py
@date: 2026-07-27
@description: Cross-driver conformance tests for the AgentLoopDriver seam.

Every registered driver (claude_code, codex_cli, plus the remote
wrapper) must present the same surface: runtime-checkable Protocol
conformance, a keyword-only ``streaming`` flag, and the capability
negotiation hook. These tests exist so a divergence (like codex's old
positional ``streaming`` or its silently discarded ``**kwargs``) fails
CI instead of surviving as an implicit inconsistency.
"""
from __future__ import annotations

import inspect

import pytest

from xyz_agent_context.agent_framework.adapters.claude.sdk import ClaudeAgentSDK
from xyz_agent_context.agent_framework.adapters.nexus.nexus_agent import NexusAgent
from xyz_agent_context.agent_framework.loop.driver import AgentLoopDriver
from xyz_agent_context.agent_framework.loop.remote_driver import (
    RemoteAgentLoopDriver,
)

try:
    from xyz_agent_context.agent_framework.adapters.codex.official_sdk import (
        CodexSDKv2,
    )

    _CODEX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _CODEX_AVAILABLE = False

# The full planned capability vocabulary from AgentLoopDriver.capabilities().
# Every capability any driver declares must come from here — a typo like
# "Steering" or "steer" would silently make the orchestrator treat a run as
# non-steerable (see test below), so pin the vocabulary in CI.
_CAPABILITY_VOCABULARY = {
    "steering", "plan", "resume", "fork", "sleep", "subagent_announce",
    "event_log", "interrupt_soft", "raw_context", "arg_streaming",
}


def _drivers():
    ds = [
        ClaudeAgentSDK(working_path="./"),
        RemoteAgentLoopDriver(
            framework="claude_code",
            working_path="./",
            executor_url="http://127.0.0.1:9",
        ),
        # working_path="/tmp" and NO warmup() — construction alone must not spawn
        # a runner process (prewarm needs a running loop, which these sync tests
        # lack, so it self-skips). Keeps the #312 test-duration win.
        NexusAgent(working_path="/tmp"),
    ]
    if _CODEX_AVAILABLE:
        ds.append(CodexSDKv2(working_path="./"))
    return ds


# ---------------- Protocol conformance ------------------------------


def test_all_drivers_satisfy_runtime_checkable_protocol():
    for d in _drivers():
        assert isinstance(d, AgentLoopDriver), type(d).__name__


# ---------------- capabilities() negotiation hook -------------------


def test_all_drivers_declare_only_known_vocabulary():
    """The negotiation seam has a live consumer now (the orchestrator gates
    steerability on ``"steering" in capabilities()``). A driver may declare
    capabilities, but only strings from the planned vocabulary — anything else
    is a typo that would silently mis-negotiate."""
    for d in _drivers():
        caps = d.capabilities()
        assert isinstance(caps, set), type(d).__name__
        unknown = caps - _CAPABILITY_VOCABULARY
        assert not unknown, (
            f"{type(d).__name__} declares unknown capabilities {unknown!r} — "
            f"not in the planned vocabulary {_CAPABILITY_VOCABULARY!r}"
        )


def test_steering_capability_is_declared_exactly_where_it_can_be_honored():
    """The contract that gates live steering: NexusAgent CAN carry a live steer
    channel (in-process queue / subprocess stdin pump) and MUST declare
    ``steering``; the remote HTTP driver CANNOT (no wire representation for a
    live channel) and MUST NOT — the orchestrator degrades a remote run to a
    fresh turn precisely because this string is absent there. A typo on either
    side ('Steering', 'steer') would silently flip the gate."""
    assert "steering" in NexusAgent(working_path="/tmp").capabilities()
    remote = RemoteAgentLoopDriver(
        framework="claude_code",
        working_path="./",
        executor_url="http://127.0.0.1:9",
    )
    assert remote.capabilities() == set()


def test_protocol_declares_capabilities_default():
    assert hasattr(AgentLoopDriver, "capabilities")


# ---------------- signature conformance -----------------------------


@pytest.mark.parametrize(
    "cls",
    [ClaudeAgentSDK, RemoteAgentLoopDriver]
    + ([CodexSDKv2] if _CODEX_AVAILABLE else []),
)
def test_agent_loop_streaming_is_keyword_only(cls):
    """The Protocol marks everything after mcp_servers keyword-only.
    A positionally callable ``streaming`` invites call-site drift."""
    sig = inspect.signature(cls.agent_loop)
    param = sig.parameters["streaming"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{cls.__name__}.agent_loop's 'streaming' must be keyword-only "
        f"(got {param.kind})"
    )


# ---------------- @timed instrumentation stays on agent_loop --------


def _timed_classes():
    """Driver classes whose agent_loop carries @timed instrumentation.
    (RemoteAgentLoopDriver is deliberately un-instrumented.)"""
    from xyz_agent_context.agent_framework.adapters.codex.cli_sdk import CodexSDK

    classes = [ClaudeAgentSDK, CodexSDK]
    if _CODEX_AVAILABLE:
        classes.append(CodexSDKv2)
    return classes


def test_agent_loop_keeps_timed_instrumentation():
    """PR #167 review catch: inserting capabilities() between the @timed
    decorator and agent_loop silently moved the latency metric + 15s
    slow-call WARNING onto capabilities(). @timed uses functools.wraps,
    so the wrapper exposes __wrapped__ — pin that agent_loop has it and
    capabilities does NOT."""
    for cls in _timed_classes():
        assert hasattr(cls.agent_loop, "__wrapped__"), (
            f"{cls.__name__}.agent_loop lost its @timed wrapper — the "
            f"llm.*.agent_loop latency metric and slow-call WARNING are gone"
        )
        assert not hasattr(cls.capabilities, "__wrapped__"), (
            f"{cls.__name__}.capabilities is decorated — @timed is "
            f"misplaced and mislabels the metric"
        )


# ---------------- codex must not silently discard kwargs ------------


@pytest.mark.skipif(not _CODEX_AVAILABLE, reason="openai-codex not installed")
@pytest.mark.asyncio
async def test_codex_warns_on_unsupported_kwargs(monkeypatch, caplog):
    """CodexSDKv2 used to ``del kwargs`` outright, silently discarding
    ``disallowed_tools`` — the caller believed the constraint was
    applied. The driver still doesn't implement it, but it must say so
    loudly instead of eating it."""
    import sys

    from loguru import logger as loguru_logger

    # Force the lazy SDK import to fail so the generator raises right
    # after the kwargs handling — no real Codex process is spawned.
    monkeypatch.setitem(sys.modules, "openai_codex", None)

    records: list[str] = []
    sink_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        gen = CodexSDKv2(working_path="./").agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            mcp_servers={},
            disallowed_tools=["WebSearch"],
        )
        with pytest.raises(RuntimeError):
            await gen.__anext__()
    finally:
        loguru_logger.remove(sink_id)

    # Note: assert on the explicit warning text, not the bare kwarg name —
    # the @timed decorator's ERROR traceback dump also contains the kwarg
    # name and would false-positive a looser assertion.
    assert any(
        "ignoring unsupported kwargs" in r and "disallowed_tools" in r
        for r in records
    ), (
        "expected a WARNING naming the ignored kwarg 'disallowed_tools', "
        f"got: {records!r}"
    )
