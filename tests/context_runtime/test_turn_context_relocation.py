"""
@file_name: test_turn_context_relocation.py
@author: NarraNexus
@date: 2026-07-25
@description: R4a turn-context relocation — per-turn volatile content
(temporal block, narrative updated_at/current_summary, recent background
activity, module get_turn_context blocks) moves out of the system prompt
into a "[Turn context]" block prepended to the CURRENT user message, so
the system prompt stays byte-stable across turns (provider prefix caches).

Locks the R4a contract:
- kill-switch OFF  → assembly byte-identical to the pre-R4 layout
  (temporal + full narrative template in the system prompt, recent actions
  appended to the system prompt, current user message == input_content);
- kill-switch ON   → volatile sections appear ONLY in the LLM-facing
  current message, in a fixed order, and ``ctx_data.input_content`` (the
  string ChatModule.hook_persist_turn persists and the frontend renders)
  is NEVER touched;
- module get_turn_context blocks: deduplicated by module_class, stable
  priority-ascending order, per-module fail-open;
- sys_sha256 instrumentation: the [SYSPROMPT-BREAKDOWN] line hashes the
  final adapter-facing system prompt — stable across turns when the flag
  is on (temporal excluded), varying when the flag is off.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from loguru import logger

from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.context_runtime.prompts import (
    TURN_CONTEXT_HEADER,
    USER_MESSAGE_SEPARATOR,
)
from xyz_agent_context.narrative.models import (
    Narrative,
    NarrativeActor,
    NarrativeActorType,
    NarrativeInfo,
    NarrativeType,
)
from xyz_agent_context.schema import ContextData
from xyz_agent_context.schema.module_schema import ModuleInstructions
from xyz_agent_context.settings import settings


AGENT_ID = "agent_tcr"
USER_ID = "u_tcr"


def _narrative(summary: str = "Topic: testing", updated: datetime | None = None) -> Narrative:
    created = datetime(2026, 7, 20, 8, 0, 0)
    return Narrative(
        id="narr_tcr",
        type=NarrativeType.CHAT,
        agent_id=AGENT_ID,
        narrative_info=NarrativeInfo(
            name="Relocation test",
            description="R4a",
            current_summary=summary,
            actors=[NarrativeActor(id=AGENT_ID, type=NarrativeActorType.AGENT)],
        ),
        event_ids=[],
        created_at=created,
        updated_at=updated or datetime(2026, 7, 25, 9, 30, 0),
    )


def _ctx_data(**extra) -> ContextData:
    ctx = ContextData(
        agent_id=AGENT_ID,
        user_id=USER_ID,
        input_content="what time is it?",
        working_source="chat",
    )
    ctx.extra_data = {
        "recent_actions": [
            {
                "timestamp": "2026-07-25T09:00:00",
                "working_source": "job",
                "title": "nightly digest",
                "event_id": "evt_bg1",
            }
        ],
        **extra,
    }
    return ctx


async def _runtime(db_client, monkeypatch) -> ContextRuntime:
    """Minimal ContextRuntime over the test DB, with the shared factory
    (used by PromptBuilder actor resolution) redirected to the same DB."""
    import xyz_agent_context.utils.db.db_factory as dbf

    async def _fake_db():
        return db_client

    monkeypatch.setattr(dbf, "get_db_client", _fake_db)

    await db_client.insert("users", {
        "user_id": USER_ID,
        "display_name": "tcr_user",
        "user_type": "user",
        "timezone": "Asia/Shanghai",
        "status": "active",
    })

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = AGENT_ID
    runtime.user_id = USER_ID
    return runtime


async def _build(runtime: ContextRuntime, narrative: Narrative, ctx: ContextData):
    """Run the two assembly stages exactly as ContextRuntime.run() does."""
    system_prompt = await runtime.build_complete_system_prompt(
        narrative_list=[narrative],
        selected_events=[],
        module_instructions_list=[
            ModuleInstructions(name="ChatModule", instruction="stay helpful", priority=1)
        ],
        ctx_data=ctx,
    )
    final_messages, _mcp, _dis = await runtime.build_input_for_framework(
        messages=[],
        system_prompt=system_prompt,
        active_instances=[],
        ctx_data=ctx,
        narrative_list=[narrative],
    )
    return system_prompt, final_messages


# =========================================================================
# Kill-switch OFF — byte-identical to the pre-R4 layout
# =========================================================================

@pytest.mark.asyncio
async def test_flag_off_keeps_legacy_layout_byte_identical(db_client, monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", False)
    runtime = await _runtime(db_client, monkeypatch)
    narrative = _narrative()
    ctx = _ctx_data()

    system_prompt, final_messages = await _build(runtime, narrative, ctx)

    # Temporal block + FULL narrative template render live in the system prompt.
    assert "## User Temporal Context" in system_prompt
    from xyz_agent_context.narrative._narrative_impl.prompt_builder import PromptBuilder
    legacy_narrative_render = await PromptBuilder.build_main_prompt(narrative)
    assert legacy_narrative_render.strip() in system_prompt
    assert f"- Updated At: {narrative.updated_at}" in system_prompt
    assert f"- Current Summary: {narrative.narrative_info.current_summary}" in system_prompt

    # Recent actions stay a system-prompt section.
    assert "Recent background activity" in final_messages[0]["content"]

    # The current user message is EXACTLY input_content — no turn context.
    assert final_messages[-1]["role"] == "user"
    assert final_messages[-1]["content"] == "what time is it?"
    assert TURN_CONTEXT_HEADER not in final_messages[-1]["content"]


# =========================================================================
# Kill-switch ON — relocation into the current message
# =========================================================================

@pytest.mark.asyncio
async def test_flag_on_relocates_volatile_sections_into_current_message(db_client, monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = await _runtime(db_client, monkeypatch)
    narrative = _narrative()
    ctx = _ctx_data()

    system_prompt, final_messages = await _build(runtime, narrative, ctx)
    enhanced_system = final_messages[0]["content"]
    user_msg = final_messages[-1]["content"]

    # System prompt: every volatile section is gone...
    assert "## User Temporal Context" not in enhanced_system
    assert "- Updated At:" not in enhanced_system
    assert "- Current Summary:" not in enhanced_system
    assert "Recent background activity" not in enhanced_system
    # ...but the stable narrative half is still there (identity stays in prefix).
    assert f"- Narrative ID: {narrative.id}" in system_prompt
    assert "- Name: Relocation test" in system_prompt

    # Current message: turn context present, in fixed order, before the
    # separator; the user's words follow the separator.
    assert user_msg.startswith(TURN_CONTEXT_HEADER)
    i_temporal = user_msg.index("## User Temporal Context")
    i_narrative = user_msg.index("## Current narrative state")
    i_recent = user_msg.index("Recent background activity")
    i_sep = user_msg.index(USER_MESSAGE_SEPARATOR)
    i_text = user_msg.index("what time is it?")
    assert 0 < i_temporal < i_narrative < i_recent < i_sep < i_text

    # Relocated, never dropped (铁律 #16): timezone + narrative volatile
    # values + background activity all reach the model this turn.
    assert "Asia/Shanghai" in user_msg
    assert str(narrative.updated_at) in user_msg
    assert narrative.narrative_info.current_summary in user_msg
    assert "nightly digest" in user_msg

    # The persisted string is untouched — chat history / frontend never
    # see the turn context (ChatModule persists params.input_content).
    assert ctx.input_content == "what time is it?"
    assert TURN_CONTEXT_HEADER not in ctx.input_content


@pytest.mark.asyncio
async def test_flag_on_history_rows_unchanged(db_client, monkeypatch):
    """The turn context is prepended ONLY to the current-turn message —
    historical timeline rows are rendered exactly as before."""
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = await _runtime(db_client, monkeypatch)
    ctx = _ctx_data()
    ctx.chat_history = [{
        "role": "user",
        "content": "earlier question",
        "meta_data": {"timestamp": "2026-07-24T10:00:00", "narrative_id": "narr_tcr"},
    }]

    _sp, final_messages = await _build(runtime, _narrative(), ctx)

    history_rows = final_messages[1:-1]
    assert len(history_rows) == 1
    assert "earlier question" in history_rows[0]["content"]
    assert TURN_CONTEXT_HEADER not in history_rows[0]["content"]


# =========================================================================
# Module get_turn_context plumbing
# =========================================================================

class _FakeModule:
    def __init__(self, name: str, priority: int, block: str, fail: bool = False):
        self.config = SimpleNamespace(name=name, priority=priority)
        self.block = block
        self.fail = fail
        self.calls = 0

    async def get_turn_context(self, ctx_data) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("volatile source exploded")
        return self.block


def _inst(module: _FakeModule) -> SimpleNamespace:
    return SimpleNamespace(module_class=module.config.name, module=module, instance_id="i")


@pytest.mark.asyncio
async def test_module_blocks_priority_order_dedupe_and_fail_open():
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.agent_id = AGENT_ID

    low = _FakeModule("LowPriority", 5, "## Low block")
    high = _FakeModule("HighPriority", 2, "## High block")
    boom = _FakeModule("Exploding", 1, "never", fail=True)
    empty = _FakeModule("Silent", 3, "")
    dup = _FakeModule("HighPriority", 2, "## Duplicate — must not appear")

    ctx = ContextData(agent_id=AGENT_ID, user_id=None, input_content="hi")
    block = await runtime._build_turn_context_block(
        [_inst(low), _inst(boom), _inst(high), _inst(empty), _inst(dup)],
        ctx,
        narrative_list=None,
    )

    # Priority ascending regardless of instance order.
    assert block.index("## High block") < block.index("## Low block")
    # Failing module skipped, others intact (fail-open).
    assert "never" not in block
    # Empty contributions add nothing.
    assert "Silent" not in block
    # Dedup by module_class: the second HighPriority instance is not called.
    assert dup.calls == 0
    assert "Duplicate" not in block
    assert block.startswith(TURN_CONTEXT_HEADER)


@pytest.mark.asyncio
async def test_base_module_get_turn_context_defaults_to_empty():
    from xyz_agent_context.module.base import XYZBaseModule

    ctx = ContextData(agent_id=AGENT_ID, user_id=None, input_content="hi")
    # Unbound call: the default implementation must not depend on self state.
    assert await XYZBaseModule.get_turn_context(object(), ctx) == ""


# =========================================================================
# sys_sha256 instrumentation
# =========================================================================

def _capture_hashes():
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="INFO")
    return lines, sink_id


def _extract_hash(lines: list[str]) -> str:
    for line in reversed(lines):
        if "[SYSPROMPT-BREAKDOWN]" in line:
            m = re.search(r"sys_sha256=([0-9a-f]{12})", line)
            assert m, f"no sys_sha256 in: {line}"
            return m.group(1)
    raise AssertionError("no [SYSPROMPT-BREAKDOWN] line captured")


async def _hash_for_time(runtime, narrative, monkeypatch, fake_now: datetime) -> str:
    import xyz_agent_context.utils.timezone as tz_mod

    monkeypatch.setattr(tz_mod, "utc_now", lambda: fake_now)
    lines, sink_id = _capture_hashes()
    try:
        await _build(runtime, narrative, _ctx_data())
    finally:
        logger.remove(sink_id)
    return _extract_hash(lines)


@pytest.mark.asyncio
async def test_sys_sha256_stable_across_time_when_flag_on(db_client, monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = await _runtime(db_client, monkeypatch)
    narrative = _narrative()

    h1 = await _hash_for_time(
        runtime, narrative, monkeypatch, datetime(2026, 7, 25, 3, 0, 0, tzinfo=dt_timezone.utc)
    )
    h2 = await _hash_for_time(
        runtime, narrative, monkeypatch, datetime(2026, 7, 25, 3, 7, 42, tzinfo=dt_timezone.utc)
    )
    # Temporal now lives in the message, not the system prompt → same hash.
    assert h1 == h2


@pytest.mark.asyncio
async def test_sys_sha256_varies_across_time_when_flag_off(db_client, monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", False)
    runtime = await _runtime(db_client, monkeypatch)
    narrative = _narrative()

    h1 = await _hash_for_time(
        runtime, narrative, monkeypatch, datetime(2026, 7, 25, 3, 0, 0, tzinfo=dt_timezone.utc)
    )
    h2 = await _hash_for_time(
        runtime, narrative, monkeypatch, datetime(2026, 7, 25, 3, 7, 42, tzinfo=dt_timezone.utc)
    )
    # Legacy layout: the second-resolution temporal block breaks the prefix.
    assert h1 != h2
