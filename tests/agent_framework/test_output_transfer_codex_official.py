"""
@file_name: test_output_transfer_codex_official.py
@date: 2026-06-04
@description: Tests for the ``codex_official`` transfer_type branch
in output_transfer.py.

Each test takes one official-SDK Notification dict (in the shape
``{"method": "...", "payload": {...}}``) and asserts the
post-translation event(s) match the OpenAI-Agents-SDK shape that
``response_processor.ResponseProcessor.process`` consumes.

The CodexSDKv2 path emits richer events than the v1 ``codex_cli``
branch — most notably ``ReasoningSummaryTextDeltaNotification`` streams
the Thinking-panel text. The flagship test here is
``test_reasoning_summary_delta_emits_thinking_delta``.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.output_transfer import output_transfer


def _t(message: dict) -> list[dict]:
    return output_transfer(message, transfer_type="codex_official")


# ---------------- Info-only / drop ---------------------------------


def test_thread_started_dropped():
    assert _t({"method": "thread/started", "payload": {"thread_id": "abc"}}) == []


def test_turn_started_dropped():
    assert _t({"method": "turn/started", "payload": {"turn_id": "t1"}}) == []


def test_unknown_method_dropped():
    """Forward-compat: novel notifications from future SDK releases
    should not raise, just be dropped quietly with a DEBUG log."""
    assert _t({"method": "future/unknown_thing", "payload": {"x": 1}}) == []


def test_non_dict_input_returns_empty():
    """Defensive: a stringified line that parses as a non-dict
    (e.g. ``"hello"`` or ``[]``) should not crash."""
    assert output_transfer("a-string", transfer_type="codex_official") == []
    assert output_transfer([], transfer_type="codex_official") == []


# ---------------- Streaming deltas (the v2 UX win) ------------------


def test_agent_message_delta_emits_text_delta():
    """Text token streaming — fills the assistant bubble char-by-char."""
    evs = _t({
        "method": "turn/agentMessageDelta",
        "payload": {"delta": "hello world"},
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.text.delta"
    assert evs[0]["data"]["delta"] == "hello world"


def test_empty_agent_message_delta_dropped():
    """Empty delta = no-op event — don't pollute the stream."""
    evs = _t({
        "method": "turn/agentMessageDelta",
        "payload": {"delta": ""},
    })
    assert evs == []


def test_reasoning_summary_delta_emits_thinking_delta():
    """*** FLAGSHIP TEST ***

    This is the UX win v2 brings over v1: reasoning summary streams
    progressively into the Thinking panel rather than landing as one
    big block. Locked in via assertion on the ``thinking_delta``
    event shape ResponseProcessor's ThinkingBatcher consumes.
    """
    evs = _t({
        "method": "turn/reasoningSummaryTextDelta",
        "payload": {
            "delta": "The user wants me to ",
            "item_id": "rs_1",
            "summary_index": 0,
        },
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "run_item_stream_event"
    assert evs[0]["item"]["type"] == "thinking_delta"
    assert evs[0]["item"]["tool_call_id"] == "rs_1"
    assert evs[0]["item"]["content"] == "The user wants me to "


def test_reasoning_text_delta_emits_thinking_delta():
    """Reasoning text delta (the model's "raw" reasoning channel,
    when the SDK exposes it). Same thinking_delta shape as summary
    deltas."""
    evs = _t({
        "method": "turn/reasoningTextDelta",
        "payload": {"delta": "step 1: ", "item_id": "r_1"},
    })
    assert len(evs) == 1
    assert evs[0]["item"]["type"] == "thinking_delta"
    assert evs[0]["item"]["content"] == "step 1: "


def test_reasoning_summary_part_added_emits_thinking_delta_with_newlines():
    """A new ``part`` (section header) added to the reasoning summary
    is rendered as a thinking_delta with surrounding newlines so the
    UI separates sections."""
    evs = _t({
        "method": "turn/reasoningSummaryPartAdded",
        "payload": {"text": "Calculating step by step", "item_id": "rs_1"},
    })
    assert len(evs) == 1
    assert evs[0]["item"]["type"] == "thinking_delta"
    # Wrap in newlines so the UI naturally separates sections.
    assert "\nCalculating step by step\n" in evs[0]["item"]["content"]


# ---------------- Turn lifecycle ------------------------------------


def test_turn_completed_emits_response_done_with_usage():
    evs = _t({
        "method": "turn/completed",
        "payload": {
            "turn": {"usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_input_tokens": 25,
            }},
        },
    })
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.done"
    usage = evs[0]["data"]["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["cached_input_tokens"] == 25


def test_turn_completed_with_usage_at_payload_root():
    """Defensive: in some SDK versions the usage may be at
    payload.usage instead of payload.turn.usage. Both must work."""
    evs = _t({
        "method": "turn/completed",
        "payload": {"usage": {"input_tokens": 7, "output_tokens": 3}},
    })
    usage = evs[0]["data"]["usage"]
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3


def test_turn_completed_missing_usage_defaults_to_zero():
    evs = _t({"method": "turn/completed", "payload": {}})
    assert evs[0]["data"]["usage"]["input_tokens"] == 0
    assert evs[0]["data"]["usage"]["output_tokens"] == 0


def test_turn_failed_emits_response_error():
    evs = _t({
        "method": "turn/failed",
        "payload": {"error": {"message": "rate_limited"}},
    })
    assert len(evs) == 1
    assert evs[0]["data"]["type"] == "response.error"
    assert evs[0]["data"]["error_message"] == "rate_limited"
    assert evs[0]["data"]["error_type"] == "turn.failed"


def test_top_level_error_emits_response_error():
    evs = _t({
        "method": "error",
        "payload": {"message": "connection refused", "code": "transport_closed"},
    })
    assert evs[0]["data"]["type"] == "response.error"
    assert "connection refused" in evs[0]["data"]["error_message"]
    assert evs[0]["data"]["error_type"] == "transport_closed"


# ---------------- Item lifecycle (delegates to v1 helpers) ----------


def test_item_completed_agent_message_delegates_to_v1_helper():
    """``item.completed`` notifications wrap a ThreadItem; v2's
    translator reshapes into the v1 codex_cli event shape and
    delegates so we don't duplicate item-type branching."""
    evs = _t({
        "method": "turn/itemCompleted",
        "payload": {
            "item": {
                "type": "agent_message",
                "id": "i1",
                "text": "hello",
            },
        },
    })
    # v1's translator emits a raw_response_event for agent_message.
    assert len(evs) == 1
    assert evs[0]["type"] == "raw_response_event"
    assert evs[0]["data"]["type"] == "response.text.delta"
    assert evs[0]["data"]["delta"] == "hello"


def test_item_started_mcp_tool_call_emits_tool_call_item():
    evs = _t({
        "method": "turn/itemStarted",
        "payload": {
            "item": {
                "type": "mcp_tool_call",
                "id": "m1",
                "server": "lark_module",
                "tool": "lark_status",
                "arguments": {"agent_id": "x"},
            },
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_item"
    assert item["tool_name"] == "mcp__lark_module__lark_status"
    assert item["arguments"] == {"agent_id": "x"}


def test_item_completed_mcp_tool_call_emits_tool_output():
    evs = _t({
        "method": "turn/itemCompleted",
        "payload": {
            "item": {
                "type": "mcp_tool_call",
                "id": "m1",
                "server": "lark_module",
                "tool": "lark_status",
                "result": {"success": True, "bound": False},
            },
        },
    })
    assert len(evs) == 1
    item = evs[0]["item"]
    assert item["type"] == "tool_call_output_item"
    assert item["tool_name"] == "mcp__lark_module__lark_status"
    assert "success" in item["output"]


def test_item_completed_unwraps_root_model():
    """ThreadItem is a pydantic RootModel — sometimes serializes with
    a ``"root"`` wrapper. The translator must unwrap it transparently."""
    evs = _t({
        "method": "turn/itemCompleted",
        "payload": {
            "item": {
                "root": {
                    "type": "agent_message",
                    "id": "i1",
                    "text": "hi",
                },
            },
        },
    })
    assert len(evs) == 1
    assert evs[0]["data"]["delta"] == "hi"


# ---------------- Drops that should stay drops (until UI catches up)


def test_command_execution_output_delta_dropped():
    """Bash output streaming notifications are dropped intentionally
    — the frontend doesn't render mid-command output yet; the final
    aggregated output still lands on item.completed."""
    evs = _t({
        "method": "turn/commandExecutionOutputDelta",
        "payload": {"delta": "some output"},
    })
    assert evs == []


def test_mcp_tool_progress_dropped():
    """Same reason as command output — drop until frontend supports it."""
    evs = _t({
        "method": "turn/mcpToolCallProgress",
        "payload": {"progress": 0.5},
    })
    assert evs == []
