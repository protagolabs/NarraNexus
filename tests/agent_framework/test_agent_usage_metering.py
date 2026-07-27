"""
@file_name: test_agent_usage_metering.py
@author: Bin Liang
@date: 2026-07-27
@description: Free-tier agent-usage metering for proxied models.

A Claude Code agent pointed at a proxied non-Anthropic model (the LiteLLM
free-tier gateway) gets ResultMessage.usage == 0 — so the run's tokens were
never recorded and the free-tier quota never moved. Real token usage IS present
on the streaming events (message_start → input, message_delta → output); we now
harvest it into a fallback tally and promote it in finalize() when the terminal
usage is 0. These tests pin: (1) the per-event harvest, (2) the proxied fallback,
(3) NO double-count on real Anthropic where the DONE event carries usage.
"""
from types import SimpleNamespace

from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    DATA_TYPE_USAGE,
)
from xyz_agent_context.agent_framework.loop.output_transfer import (
    _convert_result_to_stream_event,
    _convert_stream_event_to_stream_event,
)
from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor


def _start(usage):
    return SimpleNamespace(event={"type": "message_start", "message": {"usage": usage}})


def _delta(usage):
    return SimpleNamespace(
        event={"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": usage}
    )


def _result(usage, turns=None, cost=None):
    return SimpleNamespace(
        usage=usage, total_cost_usd=cost, num_turns=turns,
        stop_reason="end_turn", session_id="sess_x",
    )


def test_message_start_emits_input_only():
    ev = _convert_stream_event_to_stream_event(
        _start({"input_tokens": 100, "cache_read_input_tokens": 8})
    )
    assert ev["data"]["type"] == DATA_TYPE_USAGE
    assert ev["data"]["usage"]["input_tokens"] == 100
    assert ev["data"]["usage"]["output_tokens"] == 0
    assert ev["data"]["usage"]["cache_read_input_tokens"] == 8


def test_message_delta_emits_output_only():
    # LiteLLM echoes input_tokens on message_delta too — we must ignore it there
    # so message_start (input) + message_delta (output) don't double-count input.
    ev = _convert_stream_event_to_stream_event(
        _delta({"input_tokens": 100, "output_tokens": 40})
    )
    assert ev["data"]["type"] == DATA_TYPE_USAGE
    assert ev["data"]["usage"]["output_tokens"] == 40
    assert ev["data"]["usage"]["input_tokens"] == 0


def test_result_message_still_carries_usage_when_present():
    # Real-Anthropic path unchanged: ResultMessage.usage flows onto the DONE event.
    ev = _convert_result_to_stream_event(
        _result({"input_tokens": 120, "output_tokens": 30}, turns=3)
    )
    assert ev["data"]["type"] == DATA_TYPE_DONE
    assert ev["data"]["usage"]["input_tokens"] == 120
    assert ev["data"]["num_turns"] == 3


def _drive(events) -> ExecutionState:
    rp = ResponseProcessor()
    st = ExecutionState()
    for e in events:
        for pr in rp.process(e, st):
            st = rp.apply_state_update(st, pr)
    return st.finalize()


def test_proxied_zero_resultmessage_falls_back_to_streamed():
    """Gateway model: ResultMessage.usage == 0, but streamed deltas carry the
    real tokens → finalize promotes them so the quota actually deducts."""
    st = _drive([
        _convert_stream_event_to_stream_event(_start({"input_tokens": 1000})),
        _convert_stream_event_to_stream_event(_delta({"output_tokens": 250})),
        _convert_result_to_stream_event(_result({"input_tokens": 0, "output_tokens": 0}, turns=4)),
    ])
    assert st.input_tokens == 1000
    assert st.output_tokens == 250
    assert st.num_turns == 4


def test_real_anthropic_no_double_count():
    """DONE carries authoritative usage → streamed deltas must NOT add on top."""
    st = _drive([
        _convert_stream_event_to_stream_event(_start({"input_tokens": 1000})),
        _convert_stream_event_to_stream_event(_delta({"output_tokens": 250})),
        _convert_result_to_stream_event(_result({"input_tokens": 1200, "output_tokens": 300}, turns=4)),
    ])
    assert st.input_tokens == 1200   # ResultMessage wins (not 2200)
    assert st.output_tokens == 300   # not 550


def test_multi_turn_streamed_sums_across_turns():
    """A multi-turn agent (tool calls) emits one start/delta pair per turn;
    the fallback tally sums them."""
    st = _drive([
        _convert_stream_event_to_stream_event(_start({"input_tokens": 500})),
        _convert_stream_event_to_stream_event(_delta({"output_tokens": 100})),
        _convert_stream_event_to_stream_event(_start({"input_tokens": 700})),
        _convert_stream_event_to_stream_event(_delta({"output_tokens": 150})),
        _convert_result_to_stream_event(_result({"input_tokens": 0, "output_tokens": 0}, turns=2)),
    ])
    assert st.input_tokens == 1200
    assert st.output_tokens == 250
