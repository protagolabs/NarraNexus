"""
@file_name: test_resume_failed_threading.py
@author:
@date: 2026-07-28
@description: R3 marker chain — the adapter's response.resume_failed marker
threads response_processor → ExecutionState.resume_failed →
PathExecutionResult, WITHOUT ever becoming a user-visible message (铁律
#16: the same-turn cold retry makes the turn normal for the user). Mirrors
tests/agent_runtime/test_cli_session_id_threading.py's style. The marker
type rides the shared events.py constant so producer (claude adapter) and
consumer (response_processor) can never drift.
"""
from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_RESUME_FAILED,
    TYPE_RAW_RESPONSE_EVENT,
)
from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.schema.decision_schema import PathExecutionResult

_MARKER = {
    "type": TYPE_RAW_RESPONSE_EVENT,
    "data": {"type": DATA_TYPE_RESUME_FAILED},
}


def _run_through_processor(event: dict, state: ExecutionState | None = None):
    rp = ResponseProcessor()
    state = state or ExecutionState()
    emitted = []
    for processed in rp.process(event, state):
        state = rp.apply_state_update(state, processed)
        emitted.append(processed)
    return state, emitted


def test_marker_constant_is_the_wire_value():
    # Wire protocol: the executor's NDJSON channel streams the literal.
    assert DATA_TYPE_RESUME_FAILED == "response.resume_failed"


def test_marker_sets_state_flag_without_user_message():
    state, emitted = _run_through_processor(_MARKER)
    assert state.resume_failed is True
    # Internal signal only — nothing user-visible comes out of it.
    assert all(p.message is None for p in emitted)


def test_marker_flag_is_sticky_across_later_events():
    state, _ = _run_through_processor(_MARKER)
    # A later usage event (the cold retry's response.done) must not unset it.
    state = state.accumulate_usage(10, 5, cli_session_id="cli_retry_new")
    assert state.resume_failed is True
    assert state.cli_session_id == "cli_retry_new"


def test_state_defaults_to_not_failed():
    assert ExecutionState().resume_failed is False


def test_path_execution_result_carries_resume_failed():
    assert PathExecutionResult().resume_failed is False
    result = PathExecutionResult(resume_failed=True)
    assert result.resume_failed is True
