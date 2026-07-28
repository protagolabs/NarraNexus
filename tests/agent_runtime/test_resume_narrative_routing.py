"""
@file_name: test_resume_narrative_routing.py
@author:
@date: 2026-07-28
@description: Mid-turn narrative routing vs the CLI session handle (review
FIX 2) — the documented, deliberate tradeoff, pinned so it is not
rediscovered as a "bug".

step_3 validates the stored handle against the PRE-turn
``session.current_narrative_id``. step_4's 4.0 block can re-point
``ctx.session.current_narrative_id`` mid-turn (the agent called
``switch_narrative`` / ``create_narrative``) BEFORE 4.7 persists the handle, so
the row's ``narrative_id`` is the POST-routing narrative — not the one the gate
approved.

That is accepted and fail-open. What this file pins:
  1. 4.7 stores the POST-routing narrative (ordering: 4.0 runs before 4.7);
  2. a next turn that continues in the ROUTED thread resumes (anchors agree);
  3. a next turn whose pre-turn narrative is the ORIGINAL one cold-starts with
     ``COLD reason=narrative_changed`` — one wasted cold start, never a wrong
     resume.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from loguru import logger

from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _resolve_resume_session_id,
)
from xyz_agent_context.repository import CliSessionRepository
from xyz_agent_context.schema import ProgressMessage, ProgressStatus
from xyz_agent_context.schema.decision_schema import PathExecutionResult

step4_mod = importlib.import_module(
    "xyz_agent_context.agent_runtime._agent_runtime_steps.step_4_persist_results"
)
step1_mod = importlib.import_module(
    "xyz_agent_context.agent_runtime._agent_runtime_steps.step_1_select_narrative"
)

AGENT = "agent_routing_test"
USER = "user_routing_test"
SESS = "sess_routing_1"
ORIGINAL = "nar_original"
ROUTED = "nar_routed"
FPRINT = "0123456789abcdef"
CWD = "/data/workspaces/u1/agent_routing_test"
HANDLE = "cli_session_routed_turn"


# ---------------------------------------------------------------------------
# Fakes: just enough of step_4's collaborators
# ---------------------------------------------------------------------------


def _narrative(nid: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=nid,
        round_counter=0,
        is_special="",
        narrative_info=SimpleNamespace(name=nid),
    )


class _FakeEventService:
    async def update_event_in_db(self, **kwargs):
        return None

    async def update_event_narrative_id(self, event_id, narrative_id):
        self.last_narrative_id = narrative_id

    async def duplicate_event_for_narrative(self, event, narrative_id):
        return event


class _FakeNarrativeService:
    def __init__(self, target):
        self.target = target

    async def load_narrative_from_db(self, narrative_id):
        return self.target if narrative_id == self.target.id else None

    async def create_narrative(self, **kwargs):  # pragma: no cover - switch path only
        return self.target

    async def update_with_event(self, narrative, event, **kwargs):
        return None


class _FakeMarkdownManager:
    async def update_statistics(self, narrative_id, stats):
        return None


class _FakeTrajectoryRecorder:
    async def record_round(self, **kwargs):
        return None


class _FakeSessionService:
    def __init__(self):
        self.saved: list = []

    async def save_session(self, session):
        self.saved.append(session)


def _switch_narrative_call(target_id: str) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="tool",
        description="",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__basic_info_module__switch_narrative",
            "arguments": {"narrative_id": target_id},
        },
    )


def _ctx(session_narrative: str) -> RunContext:
    ctx = RunContext(
        agent_id=AGENT,
        user_id=USER,
        input_content="move this to the other thread",
        working_source="chat",
        session=SimpleNamespace(
            session_id=SESS,
            current_narrative_id=session_narrative,
            last_response="",
            last_query="",
            last_query_time=None,
        ),
        event=SimpleNamespace(id="evt_routing_1", final_output=""),
        narrative_list=[_narrative(ORIGINAL)],
    )
    ctx.load_result = SimpleNamespace(
        active_instances=[],
        relationship_graph="",
        changes_explanation={},
        changes_summary={},
        execution_type=SimpleNamespace(value="agent_loop"),
    )
    ctx.execution_result = PathExecutionResult(
        final_output="moved it",
        execution_steps=[],
        response_count=1,
        agent_loop_response=[_switch_narrative_call(ROUTED)],
        cli_session_id=HANDLE,
        cli_framework="claude_code",
        cli_config_fingerprint=FPRINT,
        cli_working_path=CWD,
    )
    return ctx


@pytest.fixture
def patch_step4(monkeypatch, db_client):
    async def _get_db():
        return db_client

    async def _ensure_chat_instance(agent_id, user_id, narrative_id):
        return f"chat_{narrative_id}"

    monkeypatch.setattr(step4_mod, "get_db_client", _get_db)
    monkeypatch.setattr(step1_mod, "_ensure_user_chat_instance", _ensure_chat_instance)
    return db_client


async def _run_step4(ctx: RunContext) -> None:
    session_service = _FakeSessionService()
    async for _ in step4_mod.step_4_persist_results(
        ctx,
        _FakeEventService(),
        _FakeNarrativeService(_narrative(ROUTED)),
        _FakeMarkdownManager(),
        _FakeTrajectoryRecorder(),
        session_service,
    ):
        pass


class _FakeDb:
    """CliSessionRepository.get surface for the NEXT turn's resolution."""

    def __init__(self, row):
        self.row = row

    async def get_one(self, table, filters):
        return self.row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routed_turn_stores_the_post_routing_narrative(patch_step4):
    ctx = _ctx(session_narrative=ORIGINAL)
    await _run_step4(ctx)

    # 4.0 re-pointed the session, and 4.7 (which runs after it) stored that
    # value — NOT the pre-turn narrative step_3 validated against.
    assert ctx.session.current_narrative_id == ROUTED
    handle = await CliSessionRepository(patch_step4).get(AGENT, SESS, "claude_code")
    assert handle is not None
    assert handle.narrative_id == ROUTED
    assert handle.cli_session_id == HANDLE


@pytest.mark.asyncio
async def test_next_turn_in_the_routed_thread_resumes(patch_step4):
    ctx = _ctx(session_narrative=ORIGINAL)
    await _run_step4(ctx)
    handle = await CliSessionRepository(patch_step4).get(AGENT, SESS, "claude_code")

    # The conversation continues where the agent moved it: anchors agree, so
    # the CLI session that genuinely served that thread is resumed.
    resumed = await _resolve_resume_session_id(
        agent_id=AGENT,
        session=SimpleNamespace(session_id=SESS, current_narrative_id=ROUTED),
        framework="claude_code",
        config_fingerprint=handle.config_fingerprint,
        working_path=handle.working_path,
        db_client=_FakeDb(handle.model_dump()),
    )
    assert resumed == HANDLE


@pytest.mark.asyncio
async def test_next_turn_back_in_the_original_thread_cold_starts(patch_step4):
    ctx = _ctx(session_narrative=ORIGINAL)
    await _run_step4(ctx)
    handle = await CliSessionRepository(patch_step4).get(AGENT, SESS, "claude_code")

    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(m), level="INFO")
    try:
        resumed = await _resolve_resume_session_id(
            agent_id=AGENT,
            session=SimpleNamespace(
                session_id=SESS, current_narrative_id=ORIGINAL
            ),
            framework="claude_code",
            config_fingerprint=handle.config_fingerprint,
            working_path=handle.working_path,
            db_client=_FakeDb(handle.model_dump()),
        )
    finally:
        logger.remove(sink_id)

    # Fail-open: one wasted cold start, never a resume under an unapproved
    # narrative.
    assert resumed is None
    assert any("COLD reason=narrative_changed" in line for line in lines)
