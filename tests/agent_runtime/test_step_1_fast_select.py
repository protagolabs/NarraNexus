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


def test_session_service_is_optional_and_defaults_none():
    # The ephemeral (voice) contract is now behavioral, not structural:
    # session_service exists for the durable chat path but defaults to
    # None, and ephemeral profiles never touch it (tests below).
    params = inspect.signature(mod.step_1_fast_select).parameters
    assert params["session_service"].default is None


def _session():
    return SimpleNamespace(
        session_id="sess_1",
        last_query="old query",
        current_narrative_id="nar_old",
        query_count=7,
        last_query_time=None,
    )


def _durable_profile():
    from xyz_agent_context.schema.turn_profile import TurnProfile

    return TurnProfile.fast_for("chat")


def _ephemeral_profile():
    from xyz_agent_context.schema.turn_profile import TurnProfile

    return TurnProfile.voice_fast()


@pytest.mark.asyncio
async def test_durable_miss_creates_narrative_and_anchors_session(monkeypatch):
    created = SimpleNamespace(
        id="nar_new", narrative_info=SimpleNamespace(name="New", current_summary="")
    )
    service = SimpleNamespace(
        select_fast=AsyncMock(return_value=None),
        create_fast=AsyncMock(return_value=created),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    ensure = AsyncMock(return_value="chat_i9")
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", ensure)

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.create_fast.assert_awaited_once_with(
        "agent_a", "user_u", "raw execution prompt"
    )
    assert ctx.narrative_list == [created]
    assert ctx.user_chat_instances == {"nar_new": "chat_i9"}
    assert ctx.session.current_narrative_id == "nar_new"
    assert ctx.session.last_query == "raw execution prompt"
    assert ctx.session.query_count == 8
    assert ctx.session.last_query_time is not None
    session_service.save_session.assert_awaited_once_with(ctx.session)


@pytest.mark.asyncio
async def test_durable_hit_anchors_session_without_creating(monkeypatch):
    narrative = SimpleNamespace(
        id="nar_hit", narrative_info=SimpleNamespace(name="N", current_summary="s")
    )
    service = SimpleNamespace(
        select_fast=AsyncMock(return_value=narrative),
        create_fast=AsyncMock(),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(session=_session(), turn_profile=_durable_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    service.create_fast.assert_not_awaited()
    assert ctx.session.current_narrative_id == "nar_hit"
    session_service.save_session.assert_awaited_once_with(ctx.session)


@pytest.mark.asyncio
async def test_ephemeral_miss_stays_bare_and_never_touches_session(monkeypatch):
    service = SimpleNamespace(
        select_fast=AsyncMock(return_value=None),
        create_fast=AsyncMock(),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock())

    ctx = _ctx(session=_session(), turn_profile=_ephemeral_profile())
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    assert ctx.narrative_list == []
    service.create_fast.assert_not_awaited()
    session_service.save_session.assert_not_awaited()
    assert ctx.session.current_narrative_id == "nar_old"


@pytest.mark.asyncio
async def test_durable_non_user_chat_never_touches_session(monkeypatch):
    # Background-ish sources must not overwrite the chat continuity anchor
    # even if a durable profile ever reaches them.
    narrative = SimpleNamespace(
        id="nar_hit", narrative_info=SimpleNamespace(name="N", current_summary="s")
    )
    service = SimpleNamespace(
        select_fast=AsyncMock(return_value=narrative),
        create_fast=AsyncMock(),
    )
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(return_value="c1"))

    ctx = _ctx(
        session=_session(), turn_profile=_durable_profile(), working_source="job"
    )
    await _drain(mod.step_1_fast_select(ctx, service, session_service))

    session_service.save_session.assert_not_awaited()
    assert ctx.session.current_narrative_id == "nar_old"
