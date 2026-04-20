"""
@file_name: test_run_collector.py
@author: Bin Liang
@date: 2026-04-20
@description: Unit tests for the `collect_run` helper (Bug 2 step 2/4).

`collect_run` is the single collection loop used by every non-WS consumer
of `AgentRuntime.run()` (LarkTrigger, JobTrigger, MessageBusTrigger,
ChatTrigger A2A). It groups messages by type — text deltas, tool calls,
errors, raw payloads — so each trigger only has to implement its own
"what to do with an error" policy, not re-implement the loop.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from xyz_agent_context.agent_runtime.run_collector import (
    RunCollection,
    RunError,
    collect_run,
)
from xyz_agent_context.schema.runtime_message import MessageType


class _FakeRuntime:
    """Stands in for AgentRuntime — any sequence of messages we want to
    feed collect_run."""

    def __init__(self, messages: list):
        self._messages = messages

    def run(self, **_kwargs) -> AsyncIterator:
        async def _gen():
            for m in self._messages:
                yield m
        return _gen()


def _delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.AGENT_RESPONSE,
        delta=text,
    )


def _tool(name: str, raw: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.TOOL_CALL,
        tool_name=name,
        tool_input={},
        raw=raw,
    )


def _error(error_type: str, message: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.ERROR,
        error_type=error_type,
        error_message=message,
    )


@pytest.mark.asyncio
async def test_collects_text_deltas_in_order():
    runtime = _FakeRuntime([_delta("Hello "), _delta("world"), _delta("!")])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="chat",
    )
    assert result.output_text == "Hello world!"
    assert result.error is None
    assert result.is_error is False


@pytest.mark.asyncio
async def test_collects_tool_call_names():
    runtime = _FakeRuntime([
        _delta("Let me search."),
        _tool("web_search"),
        _tool("lark_cli"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="job",
    )
    assert result.tool_calls == ["web_search", "lark_cli"]
    assert result.output_text == "Let me search."


@pytest.mark.asyncio
async def test_collects_raw_payloads():
    """Lark's `_extract_lark_reply` needs access to the underlying raw
    dict from tool_call_item events; collect_run must preserve them."""
    raw = {"item": {"type": "tool_call_item", "input": {"command": "im +messages-send --text hi"}}}
    runtime = _FakeRuntime([_tool("lark_cli", raw=raw)])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert result.raw_items == [raw]


@pytest.mark.asyncio
async def test_error_is_captured_not_silently_dropped():
    runtime = _FakeRuntime([
        _delta("Attempting..."),
        _error("SystemDefaultUnavailable", "quota exhausted"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="lark",
    )
    assert result.is_error is True
    assert result.error == RunError(
        error_type="SystemDefaultUnavailable",
        error_message="quota exhausted",
    )
    # Preserves any text that was emitted BEFORE the error — callers
    # can decide whether to show it (it's half a reply) or drop it.
    assert result.output_text == "Attempting..."


@pytest.mark.asyncio
async def test_last_error_wins_when_multiple():
    """Unusual but possible: two ERROR events. Keep the last one so the
    caller sees the most specific failure."""
    runtime = _FakeRuntime([
        _error("GenericError", "first"),
        _error("SystemDefaultUnavailable", "specific"),
    ])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="job",
    )
    assert result.error.error_type == "SystemDefaultUnavailable"
    assert result.error.error_message == "specific"


@pytest.mark.asyncio
async def test_empty_run_returns_empty_collection():
    runtime = _FakeRuntime([])
    result = await collect_run(
        runtime, agent_id="a", user_id="u",
        input_content="hi", working_source="chat",
    )
    assert result == RunCollection(
        output_text="", tool_calls=[], raw_items=[], error=None,
    )


@pytest.mark.asyncio
async def test_kwargs_forwarded_to_runtime_run():
    """Triggers pass trigger_extra_data / job_instance_id / forced_narrative_id
    etc. — collect_run must forward them verbatim."""
    seen: dict = {}

    class _CapturingRuntime:
        def run(self, **kwargs):
            seen.update(kwargs)
            async def _gen():
                if False:
                    yield None  # make this a generator
            return _gen()

    await collect_run(
        _CapturingRuntime(),
        agent_id="a", user_id="u",
        input_content="hi", working_source="job",
        job_instance_id="inst_42",
        forced_narrative_id="nar_7",
        trigger_extra_data={"k": "v"},
    )
    assert seen["agent_id"] == "a"
    assert seen["user_id"] == "u"
    assert seen["input_content"] == "hi"
    assert seen["working_source"] == "job"
    assert seen["job_instance_id"] == "inst_42"
    assert seen["forced_narrative_id"] == "nar_7"
    assert seen["trigger_extra_data"] == {"k": "v"}
