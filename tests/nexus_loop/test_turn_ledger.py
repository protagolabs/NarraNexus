"""
@file_name: test_turn_ledger.py
@author: Bin Liang
@date: 2026-07-29
@description: TurnLedger invariants: pairing, seq monotonicity, usage
accumulation, interruption synthesis, compaction substitution.
"""

import pytest

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import (
    TYPE_COMPACTION,
    TYPE_STEP_DONE,
    TYPE_TEXT_DELTA,
    TYPE_TOOL_RESULT,
    TYPE_TOOL_USE,
    TYPE_TURN_DONE,
    EndReason,
    LedgerEntry,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import ModelEvent
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolResult
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.session.turn_ledger import (
    TurnLedger,
)


def _text(text: str) -> ModelEvent:
    return ModelEvent(kind="text_delta", payload={"text": text})


def _tool_use(call_id: str, name: str, args: dict | None = None) -> ModelEvent:
    return ModelEvent(
        kind="tool_use",
        payload={"call_id": call_id, "tool_name": name, "args": args or {}},
    )


def _done(input_tokens: int = 100, output_tokens: int = 10) -> ModelEvent:
    return ModelEvent(
        kind="done",
        payload={
            "stop_reason": "tool_use",
            "usage": Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        },
    )


def test_seq_is_monotonic_and_ledger_allocated():
    ledger = TurnLedger("t1")
    events = []
    events += ledger.record_model_event(_text("thinking..."))
    events += ledger.record_model_event(_tool_use("c1", "bash", {"command": "ls"}))
    events += ledger.record_model_event(_done())
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_tool_pairing_and_provider_messages():
    ledger = TurnLedger("t1")
    ledger.record_model_event(_text("let me check"))
    ledger.record_model_event(_tool_use("c1", "read_file", {"path": "a.txt"}))
    ledger.record_model_event(_done())
    assert [c.id for c in ledger.open_tool_calls()] == ["c1"]

    ledger.record_tool_result("c1", ToolResult(call_id="c1", ok=True, content="hello"))
    assert ledger.open_tool_calls() == ()

    msgs = ledger.provider_messages()
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["tool_calls"][0]["id"] == "c1"
    assert msgs[1] == {"role": "tool", "tool_call_id": "c1", "content": "hello"}


def test_unknown_call_id_raises():
    ledger = TurnLedger("t1")
    with pytest.raises(ValueError):
        ledger.record_tool_result("nope", ToolResult(call_id="nope", ok=True))


def test_duplicate_result_raises():
    ledger = TurnLedger("t1")
    ledger.record_model_event(_tool_use("c1", "bash"))
    ledger.record_model_event(_done())
    ledger.record_tool_result("c1", ToolResult(call_id="c1", ok=True, content=""))
    with pytest.raises(ValueError):
        ledger.record_tool_result("c1", ToolResult(call_id="c1", ok=True, content=""))


def test_interruption_synthesizes_all_open_calls():
    ledger = TurnLedger("t1")
    ledger.record_model_event(_tool_use("c1", "bash"))
    ledger.record_model_event(_tool_use("c2", "read_file"))
    ledger.record_model_event(_done())
    events = ledger.synthesize_interrupted_results("interrupted by user")
    assert len(events) == 2
    assert all(e.type == TYPE_TOOL_RESULT and e.payload["synthetic"] for e in events)
    assert ledger.open_tool_calls() == ()
    # Pairing restored in the provider view too.
    roles = [m["role"] for m in ledger.provider_messages()]
    assert roles == ["assistant", "tool", "tool"]


def test_usage_accumulates_across_steps():
    ledger = TurnLedger("t1")
    ledger.record_model_event(_done(100, 10))
    ledger.record_model_event(_done(200, 20))
    total = ledger.total_usage()
    assert (total.input_tokens, total.output_tokens) == (300, 30)
    assert ledger.last_input_tokens() == 200

    done = ledger.close_turn(EndReason.NO_MORE_ACTIONS, model="m1")
    assert done.type == TYPE_TURN_DONE
    assert done.usage == total
    assert done.payload["end_reason"] == "NO_MORE_ACTIONS"
    assert done.payload["num_steps"] == 2


def test_event_types_on_expected_tracks():
    ledger = TurnLedger("t1")
    (text_ev,) = ledger.record_model_event(_text("hi"))
    assert (text_ev.track, text_ev.type) == ("ui", TYPE_TEXT_DELTA)
    (use_ev,) = ledger.record_model_event(_tool_use("c1", "bash"))
    assert (use_ev.track, use_ev.type) == ("model", TYPE_TOOL_USE)
    (done_ev,) = ledger.record_model_event(_done())
    assert done_ev.type == TYPE_STEP_DONE and done_ev.usage is not None


def test_compaction_substitutes_projection_but_keeps_history():
    ledger = TurnLedger("t1")
    ledger.record_model_event(_tool_use("c1", "bash", {"command": "cat big"}))
    ledger.record_model_event(_done())
    ledger.record_tool_result(
        "c1", ToolResult(call_id="c1", ok=True, content="x" * 5000)
    )
    result_seq = next(
        e.seq for e in ledger.entries() if e.type == TYPE_TOOL_RESULT
    )
    entry = LedgerEntry(
        seq=999,  # placeholder; ledger re-allocates on apply
        track="model",
        type=TYPE_COMPACTION,
        payload={
            "replaces_from_seq": result_seq,
            "replaces_to_seq": result_seq,
            "summary": "[bash] 5000 chars elided",
            "retained_tail_seq": result_seq + 1,
        },
    )
    events = ledger.apply_compaction([entry])
    assert events and events[0].type == TYPE_COMPACTION

    tool_msg = [m for m in ledger.provider_messages() if m["role"] == "tool"][0]
    assert tool_msg["content"] == "[bash] 5000 chars elided"
    # History remains complete on the log.
    assert any(
        e.type == TYPE_TOOL_RESULT and len(e.payload["content"]) == 5000
        for e in ledger.entries()
    )


def test_resume_base_continues_seq():
    base = (
        LedgerEntry(seq=0, track="ui", type=TYPE_TEXT_DELTA, payload={"text": "a"}),
        LedgerEntry(seq=1, track="ui", type=TYPE_TEXT_DELTA, payload={"text": "b"}),
    )
    ledger = TurnLedger("t1", base=base)
    (ev,) = ledger.record_model_event(_text("c"))
    assert ev.seq == 2
