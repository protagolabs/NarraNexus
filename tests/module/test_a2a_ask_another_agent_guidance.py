"""
@file_name: test_a2a_ask_another_agent_guidance.py
@author: NarraNexus
@date: 2026-08-01
@description: 「问问另一个 agent 在干嘛」must route to the bus, not a refusal
(P1 2026-08-02 线下, 段 06).

Two things went wrong in the incident and only one was the agent_id bug
(covered by test_mcp_caller_identity.py):

1. the model reached for ``get_contact_info`` — a contact-details lookup
   that can never answer "what are they doing" — and
2. when it errored, the agent told the user the task was impossible, even
   though ``bus_send_to_agent`` exists and triggers the target.

Tool descriptions and module instructions are the only levers that steer
tool CHOICE, so they are asserted here like any other contract. These are
deliberately behaviour-level assertions (does the guidance name the right
tool / forbid refusing) rather than exact wording, so copy edits stay cheap.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.message_bus_module.message_bus_module import (
    MessageBusModule,
)


def _bus_instructions() -> str:
    module = MessageBusModule(
        agent_id="agent_test", user_id="u", database_client=None
    )
    return "\n".join(str(p) for p in module._static_instruction_parts())


# ---------------------------------------------------------------------------
# The owner-errand playbook
# ---------------------------------------------------------------------------


def test_instructions_cover_asking_another_agent_on_the_owners_behalf():
    text = _bus_instructions()
    assert "find something out FROM another agent" in text
    # The route must name the actual capability...
    assert "bus_send_to_agent" in text
    # ...and forbid the observed failure mode.
    assert "Never answer that you are unable to reach another agent." in text


def test_instructions_steer_away_from_contact_lookup_for_this():
    """The incident's wrong turn: a contact-details tool cannot answer
    "what are they doing"."""
    text = _bus_instructions()
    assert "contact-lookup" in text or "contact details, not answers" in text


def test_instructions_require_relaying_the_reply_to_the_owner():
    """Sending the question is only half the errand — the acceptance
    criterion is 「能实际发起查询并回报」."""
    text = _bus_instructions()
    assert "send_message_to_user_directly" in text
    assert "relay" in text.lower()


def test_reply_discipline_is_not_read_as_suppressing_the_owner_report():
    """Reply Discipline tells agents to stay quiet toward PEERS; without
    this carve-out it can be over-applied to the owner report, which would
    silently swallow the answer the user asked for."""
    text = _bus_instructions()
    assert "never suppresses reporting back to your owner" in text


def test_a_missing_target_is_a_question_not_a_refusal():
    text = _bus_instructions()
    assert "that is a clarifying question, not a refusal" in text


def test_instructions_stay_byte_stable_across_calls():
    """These parts feed the cacheable system prompt — they must not vary per
    call (prefix caching is byte-wise; see get_turn_context vs
    get_instructions in module/base.py)."""
    assert _bus_instructions() == _bus_instructions()


# ---------------------------------------------------------------------------
# The tool description the model actually reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contact_info_description_disclaims_and_redirects():
    from xyz_agent_context.module.social_network_module.social_network_module import (
        SocialNetworkModule,
    )

    module = SocialNetworkModule(
        agent_id="agent_test", user_id="u", database_client=None
    )
    server = module.build_instrumented_mcp_server()
    tool = {t.name: t for t in server._tool_manager.list_tools()}["get_contact_info"]
    doc = (tool.description or "")

    assert "does NOT contact anyone" in doc
    assert "bus_send_to_agent" in doc
    # And it must still describe its own real purpose.
    assert "contact details" in doc
