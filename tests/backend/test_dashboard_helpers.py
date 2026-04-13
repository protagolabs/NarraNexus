"""
@file_name: test_dashboard_helpers.py
@description: T11 / T12 / T13 / T15 — helpers unit tests (no DB).
"""
import pytest

from backend.routes._dashboard_helpers import (
    AgentRunState,
    build_action_line,
    bucket_count,
    classify_kind,
    sort_agents,
    to_response,
)
from backend.routes._dashboard_schema import (
    OwnedAgentStatus,
    PublicAgentStatus,
)


# ---- T11: build_action_line ------------------------------------------------

def test_build_action_line_idle_returns_none():
    assert build_action_line(AgentRunState(kind="idle")) is None


def test_build_action_line_job():
    state = AgentRunState(
        kind="JOB",
        job={"title": "weekly report", "description": "summarize activity"},
    )
    line = build_action_line(state)
    assert line is not None
    assert "weekly report" in line
    assert "summarize activity" in line


def test_build_action_line_message_bus_cross_channel():
    state = AgentRunState(
        kind="MESSAGE_BUS",
        bus_msg={"src": "lark_teamA", "dst": "slack_dev", "content": "heads up"},
    )
    line = build_action_line(state)
    assert line is not None
    import re
    assert re.match(r"\U0001F4E1 \S+ \u2192 \S+: ", line)


def test_build_action_line_strips_control_chars():
    state = AgentRunState(kind="CHAT", session_msg="hi\x00there\r\nworld\x1fend")
    line = build_action_line(state)
    assert line is not None
    assert "\x00" not in line
    assert "\x1f" not in line
    assert "  " not in line  # no double spaces


def test_build_action_line_utf8_safe_truncation():
    state = AgentRunState(kind="CHAT", session_msg="\u6c49" * 200)
    line = build_action_line(state)
    assert line is not None
    assert len(line) <= 80


def test_build_action_line_fallback_for_a2a_missing_source():
    assert build_action_line(AgentRunState(kind="A2A")) == "Running (A2A)"


def test_build_action_line_fallback_for_unknown_kind():
    assert build_action_line(AgentRunState(kind="MATRIX")) == "Running (MATRIX)"


# ---- T12: sort_agents -----------------------------------------------------

def _fake_agent(agent_id, running, started, last):
    class Status:
        pass

    class A:
        pass

    a = A()
    s = Status()
    a.agent_id = agent_id
    a.status = s
    s.kind = "CHAT" if running else "idle"
    s.started_at = started
    s.last_activity_at = last
    return a


def test_sort_running_group_first_then_idle():
    agents = [
        _fake_agent("a_idle_new", False, None, "2026-04-13T10:00:00Z"),
        _fake_agent("a_run_old", True, "2026-04-13T09:00:00Z", "2026-04-13T09:05:00Z"),
        _fake_agent("a_idle_old", False, None, "2026-04-13T01:00:00Z"),
        _fake_agent("a_run_new", True, "2026-04-13T09:30:00Z", "2026-04-13T09:31:00Z"),
    ]
    out = sort_agents(agents)
    assert [a.agent_id for a in out] == [
        "a_run_new",
        "a_run_old",
        "a_idle_new",
        "a_idle_old",
    ]


def test_sort_missing_timestamps_treated_as_oldest():
    agents = [
        _fake_agent("a_null_last", False, None, None),
        _fake_agent("a_has_last", False, None, "2026-01-01T00:00:00Z"),
    ]
    out = sort_agents(agents)
    assert [a.agent_id for a in out] == ["a_has_last", "a_null_last"]


# ---- T13: classify_kind + bucket_count ------------------------------------

def test_classify_kind_covers_7():
    for ws, expected in [
        ("chat", "CHAT"), ("CHAT", "CHAT"),
        ("job", "JOB"),
        ("a2a", "A2A"),
        ("callback", "CALLBACK"),
        ("skill_study", "SKILL_STUDY"),
        ("matrix", "MATRIX"),
        ("message_bus", "MESSAGE_BUS"),
    ]:
        assert classify_kind(ws) == expected


def test_classify_kind_unknown_defaults_to_idle():
    assert classify_kind(None) == "idle"
    assert classify_kind("") == "idle"
    assert classify_kind("unknown_source") == "idle"


def test_bucket_count_ranges():
    assert bucket_count(0) == "0"
    assert bucket_count(1) == "1-2"
    assert bucket_count(2) == "1-2"
    assert bucket_count(3) == "3-5"
    assert bucket_count(5) == "3-5"
    assert bucket_count(6) == "6-10"
    assert bucket_count(10) == "6-10"
    assert bucket_count(11) == "10+"
    assert bucket_count(999) == "10+"


# ---- T15: to_response factory --------------------------------------------

def _raw_agent(agent_id, created_by, is_public, running_count=0, action_line=None, kind="idle"):
    return {
        "agent_id": agent_id,
        "name": agent_id.upper(),
        "description": None,
        "created_by": created_by,
        "is_public": is_public,
        "status": {
            "kind": kind,
            "last_activity_at": None,
            "started_at": None,
        },
        "running_count": running_count,
        "action_line": action_line,
        "sessions": [],
        "running_jobs": [],
        "pending_jobs": [],
        "enhanced": {
            "recent_errors_1h": 0,
            "token_rate_1h": None,
            "active_narratives": 0,
            "unread_bus_messages": 0,
        },
    }


def test_to_response_owned_returns_full():
    out = to_response(_raw_agent("a1", "alice", False), viewer_id="alice")
    assert isinstance(out, OwnedAgentStatus)
    assert out.owned_by_viewer is True


def test_to_response_public_strips_owner_fields():
    out = to_response(
        _raw_agent("a1", "bob", True, running_count=3, action_line="real user message", kind="CHAT"),
        viewer_id="alice",
    )
    assert isinstance(out, PublicAgentStatus)
    dumped = out.model_dump()
    assert "action_line" not in dumped
    assert "sessions" not in dumped
    assert "running_jobs" not in dumped
    assert "running_count" not in dumped
    assert dumped["running_count_bucket"] == "3-5"


def test_to_response_private_non_owned_returns_none():
    assert to_response(_raw_agent("b2", "bob", False), viewer_id="alice") is None
