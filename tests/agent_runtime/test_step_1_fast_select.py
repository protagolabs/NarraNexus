"""
@file_name: test_step_1_fast_select.py
@date: 2026-08-06
@description: step_1_fast_select — the fast-mode replacement for step_1.

Locks:
- Hit: ctx.narrative_list = [narrative], the user's ChatModule instance
  is ensured for it (history/persistence depend on this), progress
  completes with method "bm25_fast".
- Miss: ctx.narrative_list = [] and NO instance ensure — the turn runs
  bare instead of creating anything.
- The step takes no session_service — structurally incapable of session
  writes (a normal follow-up message continuity-checks exactly as if
  the voice turn never happened).
- Retrieval text honors the trigger's clean anchor over raw input.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import importlib

# The steps package re-exports the function under the same name, which
# shadows the submodule on attribute access — resolve the module itself.
mod = importlib.import_module(
    "xyz_agent_context.agent_runtime._agent_runtime_steps.step_1_fast_select"
)
from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext


def _ctx(**overrides):
    base = dict(
        agent_id="agent_a",
        user_id="user_u",
        input_content="raw execution prompt",
        working_source="chat",
    )
    base.update(overrides)
    return RunContext(**base)


async def _drain(gen):
    return [msg async for msg in gen]


@pytest.mark.asyncio
async def test_hit_fills_ctx_and_ensures_chat_instance(monkeypatch):
    narrative = SimpleNamespace(
        id="nar_1", narrative_info=SimpleNamespace(name="N", current_summary="s")
    )
    service = SimpleNamespace(select_fast=AsyncMock(return_value=narrative))
    ensure = AsyncMock(return_value="chat_i1")
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", ensure)

    ctx = _ctx(trigger_extra_data={"retrieval_anchor": "[From Bob] weather?"})
    messages = await _drain(mod.step_1_fast_select(ctx, service))

    assert ctx.narrative_list == [narrative]
    assert ctx.user_chat_instances == {"nar_1": "chat_i1"}
    service.select_fast.assert_awaited_once_with(
        "agent_a", "user_u", "[From Bob] weather?"
    )
    ensure.assert_awaited_once_with("agent_a", "user_u", "nar_1")
    assert messages[-1].status == "completed"


@pytest.mark.asyncio
async def test_miss_runs_bare(monkeypatch):
    service = SimpleNamespace(select_fast=AsyncMock(return_value=None))
    ensure = AsyncMock()
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", ensure)

    ctx = _ctx()
    await _drain(mod.step_1_fast_select(ctx, service))

    assert ctx.narrative_list == []
    assert ctx.user_chat_instances == {}
    ensure.assert_not_awaited()
    service.select_fast.assert_awaited_once_with(
        "agent_a", "user_u", "raw execution prompt"
    )


def test_signature_has_no_session_service():
    params = inspect.signature(mod.step_1_fast_select).parameters
    assert "session_service" not in params
