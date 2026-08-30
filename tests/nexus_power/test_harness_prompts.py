"""
@file_name: test_harness_prompts.py
@author: Bin Liang
@date: 2026-07-29
@description: Harness semantics (expression, stop, hooks) and prompt
assembly (byte stability, mode faces, section presence).
"""

import pytest

from xyz_agent_context.agent_framework.nexus_power.contracts.events import LoopEvent
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import ToolCall
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.expression import (
    ExpressionContract,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.hooks import (
    HookEvent,
    HookRegistry,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.steering import (
    NullSteeringInlet,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.stop import (
    NoMoreActionsStop,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.assembler import (
    PromptAssembler,
    PromptInputs,
    PromptMode,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.library import (
    NexusPowerPrompts,
)


def test_expression_contract_names_and_tagging():
    contract = ExpressionContract(
        frozenset({"mcp__chat_module__reply_owner"})
    )
    assert contract.is_expressive("mcp__chat_module__reply_owner")
    assert not contract.is_expressive("bash")
    calls = [ToolCall(id="1", name="bash", args={})]
    assert contract.turn_had_expression(calls) is False
    calls.append(
        ToolCall(id="2", name="mcp__chat_module__reply_owner", args={})
    )
    assert contract.turn_had_expression(calls) is True

    event = LoopEvent(track="ui", seq=0, type="text_delta", payload={"text": "x"})
    tagged = contract.tag_text_event(event)
    assert tagged.payload["monologue"] is True
    assert contract.tag_text_event(tagged) is tagged  # idempotent


def test_empty_expressive_list_is_legal_mute_state():
    contract = ExpressionContract(frozenset())
    assert contract.turn_had_expression(
        [ToolCall(id="1", name="anything", args={})]
    ) is False


@pytest.mark.asyncio
async def test_stop_and_steering_defaults():
    assert await NoMoreActionsStop().should_stop([], None) is True
    assert await NoMoreActionsStop().should_stop(
        [ToolCall(id="1", name="bash", args={})], None
    ) is False
    assert await NullSteeringInlet().drain() == []


@pytest.mark.asyncio
async def test_hook_failure_postures():
    registry = HookRegistry.empty()
    outcome = await registry.fire(HookEvent.PRE_TOOL_USE, {})
    assert outcome.allowed  # no listeners = free no-op

    async def boom(payload):
        raise RuntimeError("x")

    registry.on(HookEvent.PRE_TOOL_USE, boom, failure="open")
    assert (await registry.fire(HookEvent.PRE_TOOL_USE, {})).allowed is True

    registry.on(HookEvent.POST_TOOL_USE, boom, failure="closed")
    assert (await registry.fire(HookEvent.POST_TOOL_USE, {})).allowed is False


def test_prompts_namespace_is_not_instantiable():
    with pytest.raises(TypeError):
        NexusPowerPrompts()


def test_prompt_assembly_byte_stable_and_mode_faces():
    inputs = PromptInputs(
        builtin_groups=("files", "shell"),
        capability_cards="- jobs: schedule recurring work",
        capability_instructions="Job capability loaded.",
    )
    assembler = PromptAssembler()
    a = assembler.assemble(inputs, PromptMode.FULL)
    b = assembler.assemble(inputs, PromptMode.FULL)
    assert (a.stable_prefix, a.dynamic_tail) == (b.stable_prefix, b.dynamic_tail)

    assert "working narration" in a.stable_prefix
    assert "Workspace tools" in a.stable_prefix
    assert "Expandable capabilities" in a.dynamic_tail
    assert "- jobs:" in a.dynamic_tail

    minimal = assembler.assemble(inputs, PromptMode.MINIMAL)
    assert "Expandable capabilities" not in minimal.dynamic_tail
    none_face = assembler.assemble(inputs, PromptMode.NONE)
    assert len(none_face.stable_prefix) < 200

    no_builtin = assembler.assemble(PromptInputs(), PromptMode.FULL)
    assert "Workspace tools" not in no_builtin.stable_prefix

    msgs = a.messages()
    assert [m["role"] for m in msgs] == ["system", "system"]


def test_expression_contract_is_incremental():
    """Expansion may grant delivery tools mid-turn: the contract accepts
    additions, preserves declaration order (first = the default), and
    dedupes."""
    contract = ExpressionContract(
        ("mcp__chat_module__reply_owner",)
    )
    assert contract.names() == ("mcp__chat_module__reply_owner",)
    contract.add_tools(
        (
            "mcp__lark_module__lark_cli",
            "mcp__chat_module__reply_owner",  # dupe: ignored
        )
    )
    assert contract.is_expressive("mcp__lark_module__lark_cli")
    assert contract.names() == (
        "mcp__chat_module__reply_owner",
        "mcp__lark_module__lark_cli",
    )


def test_constitution_default_reply_tool_is_data_not_copy():
    """The constitution's reply-tool example is per-turn data (the first
    platform-declared expressive tool), never a hard-coded platform name."""
    assembler = PromptAssembler()
    with_default = assembler.assemble(
        PromptInputs(
            default_reply_tool="mcp__chat_module__reply_owner"
        ),
        PromptMode.FULL,
    )
    assert "mcp__chat_module__reply_owner" in with_default.stable_prefix

    mute = assembler.assemble(PromptInputs(), PromptMode.FULL)
    assert "reply_owner" not in mute.stable_prefix
    assert "reply tool" in mute.stable_prefix  # the generic rule survives


def test_reply_reminder_lists_tools_and_defers_to_message_instructions():
    reminder = NexusPowerPrompts.reply_reminder(
        (
            "mcp__chat_module__reply_owner",
            "mcp__lark_module__lark_cli",
        )
    )
    assert "mcp__chat_module__reply_owner" in reminder
    assert "mcp__lark_module__lark_cli" in reminder
    # A message carrying its own reply instruction outranks the default list.
    assert "reply instruction" in reminder
    assert NexusPowerPrompts.reply_reminder(()) == ""


def test_reply_reminder_names_the_declared_default_first():
    """Declaration order is contract (ExpressionContract: the first name
    is the turn's default reply tool) — the reminder must SAY so, not
    flatten the list. 2026-08-13 voice call: with the hierarchy flattened
    the model followed the list over the buried per-message instruction
    on 12/14 turns."""
    reminder = NexusPowerPrompts.reply_reminder(
        (
            "mcp__narramessenger_module__speak",
            "mcp__narramessenger_module__narra_reply",
        )
    )
    # The first tool is called out as THIS turn's default...
    assert "default reply tool" in reminder
    head = reminder.split("mcp__narramessenger_module__narra_reply")[0]
    assert "mcp__narramessenger_module__speak" in head
    # Anti-repeat without contradicting the VOICE register's
    # multi-segment speak contract (pipeline review Important #2):
    # consecutive continuation calls stay legal, repeats do not.
    assert "never repeat" in reminder
    assert "consecutive reply calls" in reminder
    assert "ONE reply tool" not in reminder.split("ONLY what you pass")[1]
    # ...and a single tool renders without a dangling "others" clause.
    solo = NexusPowerPrompts.reply_reminder(("mcp__chat__reply",))
    assert "mcp__chat__reply" in solo
    assert "other reply tools" not in solo


def test_prompt_pack_subclass_overrides_one_section():
    class Pack(NexusPowerPrompts):
        @classmethod
        def identity_line(cls, inputs, mode):
            return "You are Nexus-Test."

    prompt = PromptAssembler(Pack).assemble(PromptInputs(), PromptMode.FULL)
    assert "You are Nexus-Test." in prompt.stable_prefix
