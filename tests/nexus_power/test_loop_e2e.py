"""
@file_name: test_loop_e2e.py
@author: Bin Liang
@date: 2026-07-29
@description: End-to-end loop runs against a scripted fake ModelClient:
the tool round-trip, monologue-only stop, argument streaming for marker
tools, cancellation pairing, overflow-compaction retry, and the legacy
adapter's golden shapes.
"""

import dataclasses

import pytest

from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_TEXT_DELTA,
    DATA_TYPE_USAGE,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_TOOL_ARG_DELTA,
    TYPE_TOOL_RESULT,
    TYPE_TURN_DONE,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelEvent,
    ModelParams,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    PolicyContext,
    ToolAnnotations,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from xyz_agent_context.agent_framework.nexus_power.assembly import LoopAssembly
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.event_adapter import (
    LegacyEventAdapter,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.expression import (
    ExpressionContract,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.loop import (
    NexusPowerLoop,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.compaction import (
    ToolResultPruner,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.projector import (
    PassthroughProjector,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
    DefaultErrorClassifier,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.event_log import (
    NullEventLogWriter,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.turn_ledger import (
    TurnLedger,
)


class FakeModel:
    """Replays scripted step event lists; can raise between steps."""

    def __init__(self, steps, profile=None):
        self._steps = list(steps)
        self.profile = profile or ProviderProfile(name="fake", context_window=1000)
        self.requests = []

    async def stream_step(self, request):
        self.requests.append(request)
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        for event in step:
            yield event


class FakeTools:
    def __init__(self, specs=None, results=None):
        self._specs = specs or []
        self._results = results or {}
        self.executed = []

    def visible_tools(self):
        return list(self._specs)

    def spec_for(self, name):
        return next((s for s in self._specs if s.name == name), None)

    async def execute(self, call: ToolCall) -> ToolResult:
        self.executed.append(call)
        return self._results.get(
            call.name, ToolResult(call_id=call.id, ok=True, content=f"ran {call.name}")
        )


class NeverCancelled:
    def requested(self):
        return False


class CancelAfter:
    def __init__(self, checks: int):
        self._left = checks

    def requested(self):
        self._left -= 1
        return self._left < 0


def _text(t):
    return ModelEvent(kind="text_delta", payload={"text": t})


def _use(cid, name, args=None, index=0):
    return ModelEvent(kind="tool_use", content_index=index,
                      payload={"call_id": cid, "tool_name": name, "args": args or {}})


def _done(inp=10, out=2, stop="tool_use"):
    return ModelEvent(kind="done", payload={
        "stop_reason": stop, "usage": Usage(input_tokens=inp, output_tokens=out)})


def _assembly(model, tools, cancel=None, **overrides):
    assembly = LoopAssembly(
        model=model,
        tools=tools,
        projector=PassthroughProjector([{"role": "user", "content": "hi"}]),
        log=NullEventLogWriter(),
        cancel=cancel or NeverCancelled(),
        expression=ExpressionContract(frozenset({"mcp__chat__reply"})),
        errors=DefaultErrorClassifier(),
        compaction=ToolResultPruner(),
        params=ModelParams(model="fake-model"),
    )
    return dataclasses.replace(assembly, **overrides) if overrides else assembly


async def _run(assembly):
    ledger = TurnLedger("t1")
    return [e async for e in NexusPowerLoop(assembly, ledger).run_turn()], ledger


@pytest.mark.asyncio
async def test_tool_roundtrip_then_stop():
    model = FakeModel([
        [_text("checking"), _use("c1", "bash", {"command": "ls"}), _done()],
        [_text("all done"), _done(stop="end_turn")],
    ])
    tools = FakeTools([ToolSpec(name="bash", description="", input_schema={})])
    events, ledger = await _run(_assembly(model, tools))

    types = [e.type for e in events]
    assert types.count(TYPE_TURN_DONE) == 1
    assert TYPE_TOOL_RESULT in types
    assert [c.name for c in tools.executed] == ["bash"]
    done = events[-1]
    assert done.payload["end_reason"] == "NO_MORE_ACTIONS"
    assert done.usage.input_tokens == 20  # both steps accumulated
    # Second model request contains the tool round-trip messages.
    second = model.requests[1]
    roles = [m["role"] for m in second.messages]
    assert roles == ["user", "assistant", "tool"]


@pytest.mark.asyncio
async def test_marker_tool_args_stream_and_short_circuit_semantics():
    spec = ToolSpec(
        name="mcp__chat__reply",
        description="reply",
        input_schema={},
        annotations=ToolAnnotations(
            expressive=True, marker_only=True, streamable_fields=("content",)
        ),
    )
    model = FakeModel([
        [
            ModelEvent(kind="tool_use_start", content_index=0,
                       payload={"call_index": 0, "call_id": "c1",
                                "tool_name": "mcp__chat__reply"}),
            ModelEvent(kind="arg_delta", content_index=0,
                       payload={"call_index": 0, "text": '{"content":"he'}),
            ModelEvent(kind="arg_delta", content_index=0,
                       payload={"call_index": 0, "text": 'llo"}'}),
            _use("c1", "mcp__chat__reply", {"content": "hello"}, index=0),
            _done(),
        ],
        [_done(stop="end_turn")],
    ])
    tools = FakeTools([spec])
    events, _ = await _run(_assembly(model, tools))
    streamed = "".join(
        e.payload["text"] for e in events if e.type == TYPE_TOOL_ARG_DELTA
    )
    assert streamed == "hello"  # the user-visible reply, char by char


@pytest.mark.asyncio
async def test_cancellation_synthesizes_pairs_and_closes_once():
    model = FakeModel([
        [_use("c1", "bash", {"command": "sleep"}), _done()],
    ])
    tools = FakeTools([ToolSpec(name="bash", description="", input_schema={})])
    events, ledger = await _run(
        _assembly(model, tools, cancel=CancelAfter(checks=1))
    )
    types = [e.type for e in events]
    assert types.count(TYPE_TURN_DONE) == 1
    assert events[-1].payload["end_reason"] == "INTERRUPTED"
    synthetic = [e for e in events if e.type == TYPE_TOOL_RESULT]
    assert synthetic and all(e.payload["synthetic"] for e in synthetic)
    assert ledger.open_tool_calls() == ()


@pytest.mark.asyncio
async def test_overflow_compacts_and_retries_step():
    class Overflow(Exception):
        pass

    big = "x" * 5000
    model = FakeModel([
        # Step 1: create prunable history (input below the proactive
        # threshold so only the REACTIVE path is exercised).
        [_use("c1", "bash", {}), _done(inp=500)],
        # Step 2: overflow → reactive compaction → retry succeeds.
        Overflow("prompt is too long: maximum context length exceeded"),
        [_text("recovered"), _done(inp=100, stop="end_turn")],
    ])
    tools = FakeTools(
        [ToolSpec(name="bash", description="", input_schema={})],
        results={"bash": ToolResult(call_id="c1", ok=True, content=big)},
    )
    events, ledger = await _run(_assembly(
        model, tools,
        compaction=ToolResultPruner(keep_recent_results=0, min_prunable_chars=10),
    ))
    assert events[-1].payload["end_reason"] == "NO_MORE_ACTIONS"
    assert any(e.type == "compaction" for e in events)
    # The retried request projected the pruned content.
    final_request = model.requests[-1]
    tool_msgs = [m for m in final_request.messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"].startswith("[pruned]")


@pytest.mark.asyncio
async def test_unretryable_error_yields_error_and_done():
    model = FakeModel([Exception("Incorrect API key provided")])
    events, _ = await _run(_assembly(model, FakeTools()))
    types = [e.type for e in events]
    assert "error" in types and types.count(TYPE_TURN_DONE) == 1
    error = next(e for e in events if e.type == "error")
    assert error.payload["error_type"] == "authentication_failed"
    assert events[-1].payload["end_reason"] == "ERROR"


@pytest.mark.asyncio
async def test_legacy_adapter_golden_shapes():
    model = FakeModel([
        [_text("mono"), _use("c1", "bash", {"command": "ls"}), _done()],
        [_done(stop="end_turn")],
    ])
    tools = FakeTools([ToolSpec(name="bash", description="", input_schema={})])
    events, _ = await _run(_assembly(model, tools))
    adapter = LegacyEventAdapter()
    legacy = [d for e in events for d in adapter.translate(e)]

    kinds = [
        (d["type"], (d.get("data") or d.get("item"))["type"]) for d in legacy
    ]
    assert ("raw_response_event", DATA_TYPE_TEXT_DELTA) in kinds
    assert ("run_item_stream_event", ITEM_TYPE_TOOL_CALL) in kinds
    assert ("run_item_stream_event", ITEM_TYPE_TOOL_CALL_OUTPUT) in kinds
    assert ("raw_response_event", DATA_TYPE_USAGE) in kinds
    assert kinds[-1] == ("raw_response_event", DATA_TYPE_DONE)

    tool_call = next(
        d["item"] for d in legacy
        if d.get("item", {}).get("type") == ITEM_TYPE_TOOL_CALL
    )
    assert tool_call["tool_call_id"] == "c1"
    assert tool_call["tool_name"] == "bash"
    assert tool_call["arguments"] == {"command": "ls"}

    done = legacy[-1]["data"]
    assert done["usage"]["input_tokens"] == 20
    assert done["usage"]["cache_read_input_tokens"] == 0
    assert done["model"] == "fake-model"

    # Error shape uses only legacy-safe vocabulary.
    err_model = FakeModel([Exception("This model's maximum context length")])
    err_events, _ = await _run(_assembly(
        err_model, FakeTools(),
        compaction=ToolResultPruner(keep_recent_results=0),
    ))
    err_legacy = [d for e in err_events for d in adapter.translate(e)]
    error_data = next(
        d["data"] for d in err_legacy
        if d.get("data", {}).get("type") == DATA_TYPE_ERROR
    )
    assert error_data["error_type"] == "invalid_request"  # overflow mapped safely
