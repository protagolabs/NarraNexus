"""
@file_name: test_modeling.py
@author: Bin Liang
@date: 2026-07-29
@description: Modeling group: profile resolution, cache planning, chunk
translation (fake litellm stream), usage normalization, compaction.
"""

import pytest

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import Usage
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    CachePlan,
    ModelParams,
    ModelRequest,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolResult
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.modeling.compaction import (
    ToolResultPruner,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.modeling.model_client import (
    LiteLLMModelClient,
    _extract_usage,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.modeling.profiles import (
    resolve_profile,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.modeling.prompt_cache import (
    plan_cache,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.session.turn_ledger import (
    TurnLedger,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import ModelEvent


def test_profile_resolution():
    assert resolve_profile("claude-sonnet-4", "anthropic").name == "anthropic"
    assert resolve_profile("deepseek-chat", None).name == "deepseek"
    assert resolve_profile("claude-opus-x", None).name == "anthropic"
    assert resolve_profile("totally-unknown", None).name == "default"


def test_cache_plan_breakpoints_only_for_breakpoint_dialects():
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "hi"},
    ]
    anthropic = resolve_profile("claude", "anthropic")
    plan = plan_cache(messages, anthropic)
    assert plan.breakpoint_indices == (1, 2)
    assert plan_cache(messages, resolve_profile("deepseek", None)) == CachePlan()


class _FakeLitellm:
    """Replays canned chunk dicts like LitellmClient would."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.last_kwargs = None

    async def stream_chat(self, **kwargs):
        self.last_kwargs = kwargs
        for chunk in self._chunks:
            yield chunk


def _chunk(delta=None, finish=None, usage=None):
    choice = {"delta": delta or {}, "finish_reason": finish}
    return {"choices": [choice] if (delta or finish) else [], "usage": usage}


@pytest.mark.asyncio
async def test_stream_translation_text_tool_usage():
    chunks = [
        _chunk({"content": "think"}),
        _chunk({"reasoning_content": "deep"}),
        _chunk({"tool_calls": [{"index": 0, "id": "c1",
                                "function": {"name": "bash"}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": '{"command":'}}]}),
        _chunk({"tool_calls": [{"index": 0,
                                "function": {"arguments": '"ls"}'}}]}),
        _chunk(finish="tool_calls"),
        _chunk(usage={"prompt_tokens": 90, "completion_tokens": 12,
                      "cache_read_input_tokens": 40}),
    ]
    client = LiteLLMModelClient(
        resolve_profile("claude", "anthropic"), _FakeLitellm(chunks)
    )
    request = ModelRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        params=ModelParams(model="claude-x", base_url="https://api.x.com"),
    )
    events = [e async for e in client.stream_step(request)]
    kinds = [e.kind for e in events]
    assert kinds == [
        "text_delta", "thinking_delta", "tool_use_start",
        "arg_delta", "arg_delta", "tool_use", "done",
    ]
    tool_use = events[kinds.index("tool_use")]
    assert tool_use.payload["args"] == {"command": "ls"}
    assert tool_use.payload["call_id"] == "c1"
    done = events[-1]
    assert done.payload["usage"] == Usage(
        input_tokens=90, output_tokens=12, cache_read_tokens=40
    )
    # Anthropic-protocol routing for custom endpoints.
    fake = client._client
    assert fake.last_kwargs["model"] == "anthropic/claude-x"


@pytest.mark.asyncio
async def test_cache_control_injected_at_breakpoints():
    fake = _FakeLitellm([_chunk(finish="stop")])
    client = LiteLLMModelClient(resolve_profile("claude", "anthropic"), fake)
    request = ModelRequest(
        messages=[{"role": "system", "content": "S"},
                  {"role": "user", "content": "U"}],
        tools=[],
        params=ModelParams(model="claude-x"),
        cache_plan=CachePlan(breakpoint_indices=(0,)),
    )
    _ = [e async for e in client.stream_step(request)]
    sent = fake.last_kwargs["messages"]
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[1]["content"] == "U"  # untouched


def test_usage_vocabulary_normalization():
    anthropic_style = _extract_usage(
        {"prompt_tokens": 100, "completion_tokens": 5,
         "cache_read_input_tokens": 60, "cache_creation_input_tokens": 10}
    )
    assert anthropic_style == Usage(100, 5, 60, 10)
    openai_style = _extract_usage(
        {"prompt_tokens": 100, "completion_tokens": 5,
         "prompt_tokens_details": {"cached_tokens": 60}}
    )
    assert openai_style == Usage(40, 5, 60, 0)  # inclusive → exclusive


@pytest.mark.asyncio
async def test_pruner_compacts_oldest_and_protects_tail():
    ledger = TurnLedger("t1")
    for i in range(8):
        call_id = f"c{i}"
        ledger.record_model_event(ModelEvent(
            kind="tool_use",
            payload={"call_id": call_id, "tool_name": "bash", "args": {}},
        ))
        ledger.record_model_event(ModelEvent(
            kind="done",
            payload={"stop_reason": "tool_use",
                     "usage": Usage(input_tokens=100_000)},
        ))
        ledger.record_tool_result(
            call_id, ToolResult(call_id=call_id, ok=True, content=f"{i}:" + "x" * 4000)
        )
    profile = resolve_profile("qwen", None)  # 32k window → far exceeded
    pruner = ToolResultPruner(keep_recent_results=2)
    assert pruner.should_compact(ledger, profile) is True
    entries = await pruner.compact(ledger, profile)
    assert entries  # something pruned
    ledger.apply_compaction(entries)
    tool_msgs = [m for m in ledger.provider_messages() if m["role"] == "tool"]
    # The newest two results stay verbatim.
    assert tool_msgs[-1]["content"].startswith("7:")
    assert tool_msgs[-2]["content"].startswith("6:")
    assert any(m["content"].startswith("[pruned]") for m in tool_msgs)
