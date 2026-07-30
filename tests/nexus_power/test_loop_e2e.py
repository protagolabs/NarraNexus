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
    DATA_TYPE_REPLY_DELTA,
    DATA_TYPE_TEXT_DELTA,
    DATA_TYPE_USAGE,
    ITEM_TYPE_PLAN,
    ITEM_TYPE_THINKING,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_TOOL_ARG_DELTA,
    TYPE_TOOL_USE_START,
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

    def estimate_cost_usd(self, usage, model):
        return 0.0025

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
async def test_expression_arg_stream_becomes_the_user_reply():
    """The reply streams as the model writes the expression tool's
    argument — legacy shape response.reply.delta, ordered before the
    completed tool_call that repeats the same text."""
    spec = ToolSpec(
        name="mcp__chat__reply",
        description="reply",
        input_schema={},
        annotations=ToolAnnotations(expressive=True),  # no streamable_fields:
    )                                                  # assembly defaults apply
    model = FakeModel([
        [
            ModelEvent(kind="tool_use_start", content_index=0,
                       payload={"call_index": 0, "call_id": "c1",
                                "tool_name": "mcp__chat__reply"}),
            ModelEvent(kind="arg_delta", content_index=0,
                       payload={"call_index": 0, "text": '{"content":"Hel'}),
            ModelEvent(kind="arg_delta", content_index=0,
                       payload={"call_index": 0, "text": 'lo!"}'}),
            _use("c1", "mcp__chat__reply", {"content": "Hello!"}, index=0),
            _done(),
        ],
        [_done(stop="end_turn")],
    ])
    tools = FakeTools([spec])
    events, _ = await _run(_assembly(model, tools))
    legacy = [d for e in events for d in LegacyEventAdapter().translate(e)]
    replies = [
        d["data"] for d in legacy
        if d.get("data", {}).get("type") == DATA_TYPE_REPLY_DELTA
    ]
    assert "".join(r["delta"] for r in replies) == "Hello!"
    assert {r["call_id"] for r in replies} == {"c1"}
    assert {r["tool_name"] for r in replies} == {"mcp__chat__reply"}
    # The authoritative (non-pending) tool_call still follows with the
    # same text; the name-first pending item precedes it by design.
    call = next(
        d["item"] for d in legacy
        if d.get("item", {}).get("type") == ITEM_TYPE_TOOL_CALL
        and not d["item"].get("pending")
    )
    assert call["arguments"]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_non_expressive_arg_stream_stays_internal():
    spec = ToolSpec(
        name="write_file",
        description="write",
        input_schema={},
        annotations=ToolAnnotations(streamable_fields=("content",)),
    )
    model = FakeModel([
        [
            ModelEvent(kind="tool_use_start", content_index=0,
                       payload={"call_index": 0, "call_id": "c1",
                                "tool_name": "write_file"}),
            ModelEvent(kind="arg_delta", content_index=0,
                       payload={"call_index": 0, "text": '{"content":"body"}'}),
            _use("c1", "write_file", {"content": "body"}, index=0),
            _done(),
        ],
        [_done(stop="end_turn")],
    ])
    events, _ = await _run(_assembly(model, FakeTools([spec])))
    legacy = [d for e in events for d in LegacyEventAdapter().translate(e)]
    assert not [
        d for d in legacy
        if d.get("data", {}).get("type") == DATA_TYPE_REPLY_DELTA
    ]


@pytest.mark.asyncio
async def test_plan_events_stream_and_reinject_into_prompt():
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.scheduling_channel import (
        PlanState,
        SchedulingChannel,
    )

    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.turn_ledger import (
        TurnLedger,
    )

    plan = PlanState()
    side: list = []
    ledger = TurnLedger("t1")
    channel = SchedulingChannel(
        plan, lambda steps, note: side.append(ledger.record_plan(steps, note))
    )
    model = FakeModel([
        [_use("c1", "update_plan", {"steps": [
            {"step": "research", "status": "in_progress"},
            {"step": "write up", "status": "pending"},
        ], "note": "starting"}), _done()],
        [_done(stop="end_turn")],
    ])

    class PlanTools(FakeTools):
        async def execute(self, call):
            self.executed.append(call)
            result = await channel.call(call.name, call.args, None)
            return dataclasses.replace(result, call_id=call.id)

    tools = PlanTools(channel.list_tools())
    assembly = dataclasses.replace(
        _assembly(model, tools),
        projector=PassthroughProjector(
            [{"role": "user", "content": "hi"}], plan.render
        ),
        side_events=side,
    )
    events = [e async for e in NexusPowerLoop(assembly, ledger).run_turn()]

    plan_events = [e for e in events if e.type == "plan"]
    assert plan_events and plan_events[0].payload["steps"][0]["step"] == "research"
    legacy = [d for e in events for d in LegacyEventAdapter().translate(e)]
    plan_items = [
        d["item"] for d in legacy if d.get("item", {}).get("type") == ITEM_TYPE_PLAN
    ]
    assert plan_items and plan_items[0]["note"] == "starting"
    # Re-injected into the NEXT model request as a tail system message.
    tail = model.requests[-1].messages[-1]
    assert tail["role"] == "system"
    assert "[>] research" in tail["content"] and "[ ] write up" in tail["content"]


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
    # checks=3: loop-top + the two in-stream checks (_use, _done) pass;
    # the POST-STREAM boundary sees the cancel — the call is recorded
    # and open, which is exactly the pairing scenario this test locks.
    events, ledger = await _run(
        _assembly(model, tools, cancel=CancelAfter(checks=3))
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
    # Monologue text is THINKING, never the legacy assistant-text channel:
    # our plain text is private reasoning, so surfacing it as a reply
    # would show the user unfinished internal thought.
    assert ("run_item_stream_event", ITEM_TYPE_THINKING) in kinds
    assert ("raw_response_event", DATA_TYPE_TEXT_DELTA) not in kinds
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
    # The loop prices its own turn — without this the platform's cost
    # surface shows $0 for every NexusPower turn (the claude CLI reports
    # its own cost; nobody reports ours).
    assert done["total_cost_usd"] == 0.0025
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


@pytest.mark.asyncio
async def test_tool_channel_exception_still_produces_a_paired_result():
    """A raising channel must not split a tool_use/result pair.

    `execute` had no guard, so the exception flew past DISPATCH to the
    `finally`, which only closes the turn. The call stayed open on the
    ledger and the projection then carried a `tool_calls` entry with no
    answering `tool` message — the one thing this loop promises never
    happens (2026-07-29 review).
    """
    class ExplodingTools(FakeTools):
        async def execute(self, call: ToolCall) -> ToolResult:
            raise RuntimeError("channel died")

    model = FakeModel([
        [_text("calling"), _use("c1", "bash", {"command": "ls"}), _done()],
        [_text("recovered"), _done(stop="end_turn")],
    ])
    tools = ExplodingTools([ToolSpec(name="bash", description="", input_schema={})])
    events, ledger = await _run(_assembly(model, tools))

    types = [e.type for e in events]
    assert types.count(TYPE_TURN_DONE) == 1
    assert TYPE_TOOL_RESULT in types
    assert not ledger.open_tool_calls()

    # The failure is reported to the model as a result, so it can react.
    result_event = next(e for e in events if e.type == TYPE_TOOL_RESULT)
    assert result_event.payload["ok"] is False
    assert "channel died" in str(result_event.payload)

    # Every assistant tool_call is answered in the projection.
    messages = ledger.provider_messages()
    called = [c["id"] for m in messages if m.get("tool_calls") for c in m["tool_calls"]]
    answered = [m.get("tool_call_id") for m in messages if m.get("role") == "tool"]
    assert called and set(called) == set(answered)


def _use_start(cid, name, index=0):
    return ModelEvent(kind="tool_use_start", content_index=index,
                      payload={"call_index": index, "call_id": cid,
                               "tool_name": name})


@pytest.mark.asyncio
async def test_tool_name_reaches_the_ui_before_arguments_finish():
    """The arguments stream, so the name is known well before the call
    completes — the UI should be able to show "using bash" immediately.

    Without this event the frontend learns the tool name only at
    tool_use (arguments complete), leaving a dead window on a long
    argument stream where the agent is busy but the screen says nothing.
    """
    model = FakeModel([
        [_use_start("c1", "bash"), _text("thinking"),
         _use("c1", "bash", {"command": "ls"}), _done()],
        [_done(stop="end_turn")],
    ])
    tools = FakeTools([ToolSpec(name="bash", description="", input_schema={})])
    events, _ = await _run(_assembly(model, tools))

    starts = [e for e in events if e.type == TYPE_TOOL_USE_START]
    assert len(starts) == 1
    assert starts[0].payload["tool_name"] == "bash"
    assert starts[0].payload["call_id"] == "c1"
    assert starts[0].track == "ui"


def test_adapter_maps_tool_use_start_to_a_pending_tool_call():
    from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
        LoopEvent,
    )

    out = LegacyEventAdapter().translate(
        LoopEvent(seq=0, track="ui", type=TYPE_TOOL_USE_START,
                  payload={"call_id": "c1", "tool_name": "bash"})
    )
    assert len(out) == 1
    item = out[0]["item"]
    assert item["type"] == ITEM_TYPE_TOOL_CALL
    assert item["tool_name"] == "bash"
    assert item["tool_call_id"] == "c1"
    assert item["pending"] is True
    # Same key as the completed call ("arguments", the legacy contract),
    # so consumers replace by tool_call_id without a special shape.
    assert item["arguments"] == {}


def _broken_use(cid, name, parse_error, index=0):
    return ModelEvent(kind="tool_use", content_index=index,
                      payload={"call_id": cid, "tool_name": name, "args": {},
                               "parse_error": parse_error})


@pytest.mark.asyncio
async def test_truncated_tool_call_is_answered_not_executed():
    """Arguments cut by the output-token limit: the call must NOT reach
    the tool (the old path executed with empty args and surfaced
    misleading errors like ``Is a directory``); the model gets an error
    result naming the truncation and how to recover."""
    model = FakeModel([
        [_broken_use("c1", "write_file", "unterminated string at char 812"),
         _done(stop="length")],
        [_text("understood"), _done(stop="end_turn")],
    ])
    tools = FakeTools([ToolSpec(name="write_file", description="", input_schema={})])
    events, _ = await _run(_assembly(model, tools))

    assert tools.executed == []  # never dispatched
    result = next(e for e in events if e.type == TYPE_TOOL_RESULT)
    error = result.payload["error"]
    assert "truncated" in error
    assert "8192" in error  # the limit, so the model can reason about size
    assert "NOT executed" in error
    # The turn still closes normally after the model's second step.
    assert [e.type for e in events].count(TYPE_TURN_DONE) == 1


@pytest.mark.asyncio
async def test_malformed_tool_call_without_truncation_names_the_json_error():
    model = FakeModel([
        [_broken_use("c1", "bash", "expected ',' at char 40"),
         _done(stop="tool_use")],
        [_text("ok"), _done(stop="end_turn")],
    ])
    tools = FakeTools([ToolSpec(name="bash", description="", input_schema={})])
    events, _ = await _run(_assembly(model, tools))

    assert tools.executed == []
    result = next(e for e in events if e.type == TYPE_TOOL_RESULT)
    error = result.payload["error"]
    assert "malformed JSON" in error and "expected ','" in error
