"""
@file_name: test_step4_event_log_sync.py
@date: 2026-08-12
@description: The in-memory Event step 4.4 hands downstream must carry this
              turn's event_log, not an empty list.

Step 4.3 builds `event_log_entries`, writes them to the `events` row, and then
syncs exactly ONE field back onto the in-memory object:

    ctx.event.final_output = execution_result.final_output

`ctx.event.event_log` stayed at the `[]` it was created with (`_event_impl/
crud.py`), so every later consumer of `ctx.event` was told the turn ran no
steps. That half-sync has already cost us once — the 0802 "对话时序错乱"
report was phantom event rows carrying `event_log='[]'`, copied from this same
in-memory object (see `test_step4_event_attribution.py`).

It bites again at defect A1: the narrative updater now reads `event.event_log`
to put the turn's tool actions into the retrieval surface. Reading it off a
stale in-memory object makes A1 a silent no-op in production while every unit
test — which builds its own Event — stays green. These tests pin the sync so
that cannot happen quietly.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_4_persist_results import (
    step_4_persist_results,
)
from xyz_agent_context.narrative import EventService, NarrativeService
from xyz_agent_context.narrative._narrative_impl.updater import build_action_digest
from xyz_agent_context.narrative.models import TriggerType
from xyz_agent_context.schema import ExecutionPath, PathExecutionResult
from xyz_agent_context.utils.db.db_factory import get_db_client

# Shaped like a real Claude-side step (survey §3: {tool_name, arguments, …}).
EXECUTION_STEPS = [
    {"type": "thinking", "content": "where would the deploy script live"},
    {"type": "tool_call", "tool_name": "Read",
     "arguments": {"file_path": "/tmp/project/.evermemos/web.log"}},
    {"type": "tool_output", "output": "[Errno 48] Address already in use"},
    {"type": "tool_call", "tool_name": "mcp__chat_module__send_message_to_user_directly",
     "arguments": {"agent_id": "a", "user_id": "u", "content": "端口 1995 被占用"}},
    {"type": "tool_output", "output": '{"success": true}'},
]


class _RecordingMarkdown:
    async def update_statistics(self, *, narrative_id: str, stats: dict) -> None:
        pass


class _RecordingTrajectory:
    async def record_round(self, *, narrative_id: str, **_kwargs) -> None:
        pass


async def _drain(agen) -> None:
    async for _ in agen:
        pass


@pytest.fixture
async def turn(request):
    agent_id = f"agent_{abs(hash(request.node.name)) % 10**12:012d}"
    user_id = "user_log_sync"

    db = await get_db_client()
    event_service = EventService(agent_id)
    narrative_service = NarrativeService(agent_id)
    narrative_service.set_event_service(event_service)

    narrative = await narrative_service.create_narrative(
        agent_id=agent_id, user_id=user_id, title="部署脚本报错",
    )
    event = await event_service.create_event(
        agent_id=agent_id,
        user_id=user_id,
        input_content="帮我查一下部署脚本的报错",
        trigger_type=TriggerType.CHAT,
    )

    ctx = RunContext(
        agent_id=agent_id,
        user_id=user_id,
        input_content="帮我查一下部署脚本的报错",
        working_source="chat",
        event=event,
        narrative_list=[narrative],
        load_result=SimpleNamespace(
            execution_type=ExecutionPath.AGENT_LOOP,
            active_instances=[],
            relationship_graph="",
            changes_explanation={},
            changes_summary={},
        ),
        execution_result=PathExecutionResult(
            # The A1 shape: the answer went out through a tool, so the agent's
            # own final_output is a meta-comment carrying none of the nouns.
            final_output="Good — I've already sent the findings.",
            execution_steps=EXECUTION_STEPS,
            response_count=1,
        ),
    )

    yield SimpleNamespace(
        agent_id=agent_id, db=db, ctx=ctx, event=event,
        event_service=event_service, narrative_service=narrative_service,
    )


async def _run_step_4(turn) -> None:
    await _drain(
        step_4_persist_results(
            turn.ctx, turn.event_service, turn.narrative_service,
            _RecordingMarkdown(), _RecordingTrajectory(), SimpleNamespace(),
        )
    )


async def test_in_memory_event_log_is_synced_after_step_4(turn):
    """The object handed to 4.4 must describe the turn that actually ran."""
    assert turn.ctx.event.event_log == []  # premise: it starts empty

    await _run_step_4(turn)

    assert len(turn.ctx.event.event_log) == len(EXECUTION_STEPS)
    assert [e.type for e in turn.ctx.event.event_log] == [
        s["type"] for s in EXECUTION_STEPS
    ]


async def test_synced_event_log_reaches_the_action_digest(turn):
    """The A1 payoff, driven through the real step instead of a hand-built Event.

    Without the sync this digest is empty and the narrative's retrieval surface
    keeps none of the turn's nouns — the exact production no-op A1 exists to
    prevent.
    """
    await _run_step_4(turn)

    digest = build_action_digest(turn.ctx.event.event_log)

    assert digest, "no actions extracted — event_log did not reach the updater"
    assert "web.log" in digest
    assert "端口" in digest
    assert "where would the deploy script live" not in digest  # thinking dropped


async def test_in_memory_event_log_matches_the_persisted_row(turn):
    """In-memory and on-disk must not disagree about what happened."""
    await _run_step_4(turn)

    rows = await turn.db.get("events", {"event_id": turn.event.id})
    persisted = rows[0]["event_log"]

    for step in EXECUTION_STEPS:
        assert step["type"] in persisted
    assert len(turn.ctx.event.event_log) == persisted.count('"timestamp"')
