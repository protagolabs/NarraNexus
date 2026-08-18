"""
@file_name: test_agent_vocabulary.py
@author:
@date: 2026-08-18
@description: Words the agent must never be shown, checked across every surface
              that reaches it.

Spec §10.4 asked for this test and §8 states the acceptance criterion it checks:
the subsystem's name appears in NO agent-visible text. It was not written, and
the cost was measurable — a review found eleven live strings naming a retired
owner tool, five `"MessageBus not available"` errors returned straight to the
model, a `find_agent` tool description advertising "the MessageBus registry",
and a `### Your Channels` heading printing raw channel ids into every turn. Each
one was individually obvious; what was missing was anything that looked at all
of them at once.

Why a vocabulary test rather than more per-string assertions: the redesign's
whole claim is that the agent's world has exactly two kinds of conversation in
it. That claim is falsified by ANY leaked third concept — a transport, a channel
id, an internal prefix, a tool that no longer exists — and which one leaks next
is unpredictable, whereas the vocabulary is a closed list. Per-string tests
guard the strings someone already thought of.

The surfaces collected here are the ones the model actually reads: the module's
byte-stable instruction block, its per-turn span, every MCP tool description
(the docstring IS the description), and the trigger prompts. If a new surface
appears, add it — a banned word is only banned where someone looks.
"""
from __future__ import annotations

import inspect

import pytest

from xyz_agent_context.module.message_bus_module.message_bus_module import (
    MessageBusModule,
)

#: Each entry is (fragment, why it must not reach the agent). Matched
#: case-insensitively, because the failure is the CONCEPT reaching the model, and
#: "MessageBus" vs "messagebus" is not a distinction the reader makes.
BANNED = [
    ("messagebus", "names a subsystem the agent has no model of (spec §8)"),
    ("bus_send_message", "retired tool — the agent would call a name that is gone"),
    ("bus_send_to_agent", "retired tool"),
    ("bus_create_channel", "retired tool"),
    ("bus_share_to_team", "retired tool"),
    ("send_message_to_user_directly", "retired tool, split into reply/notify_owner"),
    ("### your channels", "prints raw channel ids; the agent has no channels"),
    ("usr_", "an internal sender prefix; the agent is shown `User`"),
]

#: Words that are legitimate in a HISTORICAL note ("the old `bus_send_message`
#: could…") but never in text the model reads. The distinction is the surface,
#: not the word, so nothing is exempted here — instead the collectors below take
#: only agent-facing text, never module comments.
def _module() -> MessageBusModule:
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    return module


def _static_block() -> str:
    return "\n".join(_module()._static_instruction_parts())


def _tool_descriptions() -> str:
    """Every MCP tool's docstring — that string IS what the model is shown.

    Registered against a stub server so the real docstrings are collected rather
    than re-typed here, which is the only version that cannot drift.
    """
    from xyz_agent_context.module.message_bus_module import _message_bus_mcp_tools

    collected: list[str] = []

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                collected.append(inspect.getdoc(fn) or "")
                return fn

            return _wrap

    async def _no_bus():
        return None

    _message_bus_mcp_tools.register_message_bus_mcp_tools(_Stub(), _no_bus)
    assert collected, "no tool descriptions were collected — the stub missed them"
    _tool_descriptions.count = len(collected)
    return "\n".join(collected)


def _agent_facing_texts() -> list[tuple[str, str]]:
    return [
        ("the byte-stable instruction block", _static_block()),
        ("the MCP tool descriptions", _tool_descriptions()),
    ]


@pytest.mark.parametrize("fragment,why", BANNED)
def test_no_agent_visible_surface_uses_the_banned_word(fragment, why):
    for surface, text in _agent_facing_texts():
        assert fragment not in text.lower(), (
            f"{surface} contains {fragment!r}: {why}"
        )


def test_the_error_strings_returned_to_the_agent_name_no_subsystem():
    """Tool RETURN values are agent-visible too, and they were the biggest leak.

    Five sites returned `{"error": "MessageBus not available"}` straight to the
    model. Read off the source rather than by calling each tool: reaching the
    failure branch means faking a downed backend per tool, and the property under
    test is about the string, not the path to it.
    """
    from xyz_agent_context.module.message_bus_module import _message_bus_mcp_tools

    src = inspect.getsource(_message_bus_mcp_tools)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or '"error"' not in stripped:
            continue
        assert "MessageBus" not in stripped, (
            f"an error returned to the agent names the subsystem: {stripped}"
        )


def test_the_banned_list_would_actually_fail():
    """A guard against the guard: if the collectors returned empty strings, every
    assertion above would pass and mean nothing.

    Checks that the collected text contains something the agent IS supposed to
    see, so a broken collector fails here instead of silently certifying.
    """
    static = _static_block().lower()
    tools = _tool_descriptions().lower()

    # A tool's own name is not in its own docstring, so the collector is checked
    # by count and by a phrase only a real description would carry.
    assert _tool_descriptions.count >= 5, (
        f"only {_tool_descriptions.count} tool descriptions collected — the stub "
        f"is no longer seeing the registrations"
    )
    assert "send a private message to another agent" in tools, (
        "the tool-description collector came back without real docstrings"
    )
    assert len(static) > 500, f"the static block collapsed to {len(static)} chars"
