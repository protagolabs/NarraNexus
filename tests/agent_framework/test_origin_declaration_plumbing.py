"""
@file_name: test_origin_declaration_plumbing.py
@date: 2026-08-17
@description: The origin line survives the trip from step_3 to both drivers.

Rendering it correctly is worth nothing if it never reaches the model. Two
frameworks consume it (the Claude CLI adapter appends it to the turn's user
message; NexusPower puts it in the dynamic tail), and they must emit the SAME
characters — which is why neither one composes the sentence: they are handed a
finished string.

The failure this guards is quiet in the worst way. A dropped kwarg produces a
turn that runs fine, answers fine, and simply never told the agent where it
was — you would only find it by reading a prompt dump.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.adapters.claude.prompts import (
    append_reply_reminder,
)
from xyz_agent_context.agent_framework.loop.turn_input import TurnInput


def _turn(**kw) -> TurnInput:
    return TurnInput(messages=[], mcp_servers={}, **kw)


def test_turn_input_forwards_the_line_to_drivers():
    kwargs = _turn(
        expressive_tools=("mcp__chat_module__reply_owner",),
        origin_declaration="[Origin] NarraNexus · reply with `x`",
    ).driver_kwargs()
    assert kwargs["origin_declaration"] == "[Origin] NarraNexus · reply with `x`"


def test_empty_line_emits_no_kwarg_at_all():
    """A turn that declares nothing must produce the exact legacy kwargs.

    Same rule as `expressive_tools` / `extra_accessible_roots`: an empty value
    is not passed as an empty value, it is not passed. Drivers that never heard
    of this field keep their own defaults, and a driver that HAS heard of it
    cannot mistake "" for a real declaration.
    """
    assert "origin_declaration" not in _turn().driver_kwargs()
    assert "origin_declaration" not in _turn(origin_declaration="").driver_kwargs()


def test_claude_adapter_puts_the_line_before_the_reply_rule():
    """Origin first, then the rule it qualifies.

    "Where am I" has to land before "here is how you answer", or the rule
    arrives with nothing to attach to.
    """
    out = append_reply_reminder(
        "do the thing",
        ("mcp__chat_module__reply_owner",),
        "[Origin] NarraNexus · reply with `mcp__chat_module__reply_owner`",
    )
    assert out.startswith("do the thing")
    assert out.index("[Origin]") < out.index("Reminder:")


def test_claude_adapter_without_a_declaration_is_byte_identical_to_before():
    """The origin line is additive — omitting it must change nothing else."""
    tools = ("mcp__chat_module__reply_owner",)
    assert append_reply_reminder("hi", tools, "") == append_reply_reminder("hi", tools)


def test_no_reply_surface_means_no_reminder_and_no_origin_line():
    """A mute turn stays mute even when an origin line was rendered.

    The tools tuple is the authority on whether anything is delivered this
    turn. An origin line without a surface would announce a conversation the
    agent cannot take part in.
    """
    assert append_reply_reminder("hi", (), "[Origin] NarraNexus · reply with `x`") == "hi"


def test_nexus_options_carry_the_line():
    from xyz_agent_context.agent_framework.nexus_power.contracts.options import (
        TurnOptions,
    )

    base = {"cwd": "/tmp", "model": "m"}
    assert TurnOptions(**base).origin_declaration == ""
    assert (
        TurnOptions(**base, origin_declaration="[Origin] Lark · reply with `x`")
        .origin_declaration
        == "[Origin] Lark · reply with `x`"
    )


def test_step_3_reads_the_plain_text_marker_from_the_turns_own_extras():
    """The one input to the origin line that is not derivable from the tools.

    `render_origin_declaration` refuses to name a default when the turn's reply IS
    its plain text — otherwise origin-first ordering presents some other module's
    tool as the way to answer, and on patrol that means telling the lead to message
    its owner instead of writing the room's status line.

    `test_surface_matrix.py` re-derives that flag inside the test and comments "the
    same three inputs step_3 composes it from" — so nothing asserted that step_3
    reads it from the right place. This is exactly the failure shape this file's
    own docstring describes: a dropped or misspelled kwarg produces a turn that
    runs fine and quietly loses the guarantee.

    Asserted from the source because reaching this line means standing up a whole
    run, and the property under test is which dict the value comes from, not what
    the model then does with it. `trigger_extra_data` is what
    `MessageBusTrigger._invoke_runtime` stamps and what `agent_runtime` carries
    onto the context; reading `ctx.extra_data` — which `RunContext` does not even
    have — was the first version of this line and pyright caught it.
    """
    import inspect

    from xyz_agent_context.agent_runtime._agent_runtime_steps import (
        step_3_agent_loop,
    )
    from xyz_agent_context.schema import BUS_PLAIN_TEXT_TURN_EXTRA_KEY

    src = inspect.getsource(step_3_agent_loop)

    assert "reply_is_plain_text=" in src, (
        "step_3 stopped passing the plain-text fact, so a patrol turn gets an "
        "origin line naming a tool its own prompt forbids"
    )
    assert "BUS_PLAIN_TEXT_TURN_EXTRA_KEY" in src

    # The value must come from the turn's extras, not from a module-level default
    # or a re-derivation. `_turn_extra` is `ctx.trigger_extra_data or {}`.
    call = src[src.index("reply_is_plain_text="):][:200]
    assert "_turn_extra" in call, (
        f"the marker is not read from the turn's own extras: {call!r}"
    )
    assert BUS_PLAIN_TEXT_TURN_EXTRA_KEY == "bus_plain_text_turn"
