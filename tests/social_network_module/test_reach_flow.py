"""
@file_name: test_reach_flow.py
@author:
@date: 2026-08-21
@description: The cross-channel reachability flow (PR-2): the social graph is
the single home for "who I know and how to reach them", and the instruction
that turns that data into action must survive edits.

Two halves:
  * C2 — `get_contact_info` / `format_contact_result` RETURN the reach
    (`contact_info.channels`), so an agent can search out where to reach X.
  * WS-D — the module prompt teaches the flow (search → read channels → send on
    that channel), and says so in a way that stays true: capability follows the
    agent, reach is recorded automatically, and an absent channel means "no
    recorded way", not a guessed id.
"""
from __future__ import annotations

from xyz_agent_context.module.social_network_module import format_contact_result
from xyz_agent_context.module.social_network_module.prompts import (
    SOCIAL_NETWORK_MODULE_INSTRUCTIONS,
)


# ── C2: the reach comes back out ────────────────────────────────────────────


def test_get_contact_info_returns_the_channels_reach():
    """The whole point of recording reach is being able to read it back. The
    formatted result must carry `contact_info.channels` verbatim, or the agent
    that searched for X still cannot tell where/how to reach them."""
    recall = {
        "success": True,
        "entity": {
            "entity_name": "Alice",
            "contact_info": {
                "channels": {"lark": {"id": "ou_alice", "rooms": {"agent_a": "oc_chat7"}}},
                "preferred_channel": "lark",
            },
        },
    }

    out = format_contact_result("U_alice", recall)

    assert out["success"] is True
    assert out["contact_info"]["channels"]["lark"]["rooms"]["agent_a"] == "oc_chat7"
    assert out["contact_info"]["preferred_channel"] == "lark"


# ── WS-D: the instruction that makes it usable ──────────────────────────────


def test_the_prompt_teaches_reaching_across_channels():
    """The flow has to be spelled out or the tools go unused (the PR-1 lesson:
    a capability the model is not told to use is a capability it does not find).
    """
    text = SOCIAL_NETWORK_MODULE_INSTRUCTIONS

    # It grants the capability...
    assert "not confined to the conversation that woke you" in text
    # ...points at the single source of reach...
    assert "contact_info.channels" in text
    # ...and names the three steps: search, read the room id, send on that channel.
    assert "search_social_network" in text
    assert "rooms" in text


def test_the_prompt_says_reach_is_recorded_automatically():
    """Reach recording is now automatic (InboxRecorder). The prompt must say so,
    or agents keep being told to record it by hand — the redundant, unreliable
    path this PR replaces."""
    assert "automatically" in SOCIAL_NETWORK_MODULE_INSTRUCTIONS


def test_the_prompt_forbids_guessing_an_unrecorded_id():
    """No recorded channel entry → say so, do not invent a conversation id. An
    invented id sends nowhere (or somewhere wrong) and reads as a delivery."""
    text = SOCIAL_NETWORK_MODULE_INSTRUCTIONS.lower()

    assert "recorded way to reach them" in text
