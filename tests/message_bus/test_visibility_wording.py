"""
@file_name: test_visibility_wording.py
@author:
@date: 2026-08-11
@description: The module's standing rules must be true in every room it reaches.

`_static_instruction_parts` is emitted on every bus-enabled turn with no idea
what kind of room the turn is in — that is the point of it, and the R4 cache
depends on it staying byte-identical. So anything it asserts has to hold
everywhere, and two lines did not:

  * "In group channels, you only see messages that @mention you." True for an
    ordinary bus group. False in a team room, whose turn prompt carries the
    room's full recent scrollback and says so ten lines later. Two contradictory
    claims about the same room, in the same context window.
  * "Ignored messages resurface — they stay unread and appear again next turn."
    True in a DM, where the unread list is the queue. False in a team room since
    the read cursor started advancing on a rendered turn.

The fix is not a room-type branch — that would fork the static block and cost
the byte-stability it exists for. It is to say only what is true everywhere and
leave the room-specific fact to the room's own prompt, which is the only place
that knows.
"""
from __future__ import annotations

from xyz_agent_context.module.message_bus_module.message_bus_module import (
    MessageBusModule,
)


def _static_text() -> str:
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    return "\n".join(module._static_instruction_parts())


def test_the_static_rules_do_not_claim_mentions_limit_visibility():
    """Activation and visibility are different questions; this block may only
    speak to the first."""
    text = _static_text().lower()

    assert "you only see messages that @mention you" not in text


def test_activation_semantics_are_still_stated():
    """The part that IS true everywhere has to survive the rewrite — it is what
    stops an agent assuming a passive post woke somebody."""
    text = _static_text()

    assert "only @-mentioned agents are activated" in text


def test_resurfacing_is_scoped_to_where_it_happens():
    """A team room now clears its cursor once a turn has rendered the room, so
    an unqualified "ignored messages come back" is a promise the platform stops
    keeping the moment the agent is in a team."""
    text = _static_text()

    line = next(ln for ln in text.splitlines() if "resurface" in ln.lower())
    assert "direct" in line.lower() or "dm" in line.lower()


def test_a_teammate_is_marked_as_one_in_the_known_agents_list():
    """`via_team` was computed for every peer and read by nobody.

    The list mixes teammates with every other agent the owner has, and an agent
    reaching for help has no way to tell "we are on the same team, this one is
    already in the room with me" from "a stranger I would have to DM cold".
    """
    from xyz_agent_context.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_known_agents"] = [
        {"agent_id": "agent_mate", "agent_name": "Mate",
         "agent_description": "OCR", "via_team": True},
        {"agent_id": "agent_stranger", "agent_name": "Stranger",
         "agent_description": "Unrelated", "via_team": False},
    ]

    text = "\n".join(module._volatile_context_parts(ctx))

    mate_line = next(ln for ln in text.splitlines() if "agent_mate" in ln)
    other_line = next(ln for ln in text.splitlines() if "agent_stranger" in ln)
    assert "teammate" in mate_line
    assert "teammate" not in other_line
