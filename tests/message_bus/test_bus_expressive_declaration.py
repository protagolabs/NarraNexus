"""
@file_name: test_bus_expressive_declaration.py
@date: 2026-08-04
@description: MessageBusModule declares the bus delivery tools as the turn's
reply surface — but ONLY on bus-triggered turns (NexusPower reply contract,
origin-aware declaration).

Why gating matters both ways:
- A bus-triggered turn without this declaration tells the model (via the
  framework's per-step reply reminder) that ONLY the owner-chat tool
  delivers — so finished work ends as undelivered plain text, or gets
  misdelivered to the owner's web chat (2026-08-01 briefing-squad incident).
- A chat turn WITH this declaration would invite replying to the owner via
  the bus.

2026-08-17 — what a team turn declares FLIPPED, and the reason is worth keeping.
It used to declare NOTHING, because the room auto-posted the agent's plain text
and naming a delivery tool would have invited double-posting. That made the team
room the one surface in the system where "plain text reaches nobody" was false,
and the contradictions growing out of that exception cost six review rounds on
PR #311. The room now takes a tool call like everywhere else, so the hazard the
empty declaration guarded against no longer exists — and an empty surface would
now be the misinformation, telling the model nothing can deliver on a turn where
`message_team` is exactly what does.

Drift guards mirror tests/chat_module/test_expressive_declaration.py: the
declaration derives from get_mcp_config().server_name, and the short names
must be tools the bus MCP server actually registers.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from xyz_agent_context.module.message_bus_module.message_bus_module import (
    MessageBusModule,
)
from xyz_agent_context.schema import ContextData
from xyz_agent_context.schema.hook_schema import WorkingSource


def _module() -> MessageBusModule:
    return MessageBusModule(
        agent_id="agent_a", user_id=None, database_client=MagicMock()
    )


def _ctx(working_source, **extra) -> ContextData:
    ctx = ContextData(agent_id="agent_a", user_id=None, input_content="hi")
    ctx.working_source = working_source
    ctx.extra_data.update(extra)
    return ctx


@pytest.mark.asyncio
async def test_a_peer_dm_turn_declares_the_peer_send_verb():
    module = _module()
    mcp_config = await module.get_mcp_config()

    declared = await module.get_expressive_tools(_ctx(WorkingSource.MESSAGE_BUS))

    assert declared == [f"mcp__{mcp_config.server_name}__message_agent"]


@pytest.mark.asyncio
async def test_a_team_turn_declares_the_room_send_verb():
    """FLIPPED 2026-08-17 — see the module docstring.

    The old assertion was `declared == []`, guarding against advertising a
    delivery tool on a surface whose plain text auto-posted. That surface is
    gone; declaring nothing here would now tell the model no tool can deliver on
    the one turn where `message_team` is what does.
    """
    module = _module()
    mcp_config = await module.get_mcp_config()

    declared = await module.get_expressive_tools(
        _ctx(WorkingSource.MESSAGE_BUS, bus_team_room=True)
    )

    assert declared == [f"mcp__{mcp_config.server_name}__message_team"]


@pytest.mark.asyncio
async def test_exactly_one_verb_per_surface():
    """Exactly one DEFAULT reply reminder per turn.

    The reminder points at a single verb — the one for the conversation that
    woke the turn — so the model is never nudged two ways at once. This is about
    the DEFAULT, not about what is reachable: the other verbs stay on the desk
    (see the desk tests below), the reminder just does not name them.
    """
    module = _module()

    for extra in ({}, {"bus_team_room": True}):
        declared = await module.get_expressive_tools(
            _ctx(WorkingSource.MESSAGE_BUS, **extra)
        )
        assert len(declared) == 1, declared


@pytest.mark.asyncio
async def test_bus_turn_with_serialized_source_string_also_declares():
    module = _module()
    declared = await module.get_expressive_tools(
        _ctx(WorkingSource.MESSAGE_BUS.value)
    )
    assert any(t.endswith("__message_agent") for t in declared)


@pytest.mark.asyncio
async def test_chat_turn_declares_nothing():
    assert await _module().get_expressive_tools(_ctx(WorkingSource.CHAT)) == []


@pytest.mark.asyncio
async def test_no_ctx_declares_nothing():
    """Legacy/no-ctx callers keep the empty default — never advertise bus
    tools as the reply surface of a turn whose origin is unknown."""
    assert await _module().get_expressive_tools() == []


@pytest.mark.asyncio
async def test_declared_short_names_are_actually_registered():
    module = _module()
    mcp = module.create_mcp_server()
    assert mcp is not None
    registered = {t.name for t in await mcp.list_tools()}
    assert {"message_agent", "message_team"} <= registered


# ── the desk: the reminder defaults, the desk keeps every verb ──────────────
#
# 2026-08-20 — capability follows the agent, not the trigger channel. The
# trigger channel decides ONLY which verb the reply reminder defaults to
# (get_expressive_tools). It no longer removes the other verb: every internal
# send verb stays reachable on every bus turn, so an agent woken in a DM can
# still post in a team room it belongs to. The one turn that still clears both
# verbs is patrol, which delivers by speaking and calls no send tool.


@pytest.mark.asyncio
async def test_a_bus_turn_suppresses_neither_internal_send_verb():
    """The trigger-channel drop is gone.

    On a DM turn `message_team` stays on the desk; on a team turn
    `message_agent` does. Re-introducing the drop — keying suppression on the
    trigger channel — turns this red, which is exactly the regression the
    redesign removes: an agent asked in one conversation to act in another
    could not reach the tool for it.
    """
    for extra in ({}, {"bus_team_room": True}):
        module = _module()  # fresh per case: no state may carry between turns
        config = await module.get_mcp_config()
        q = f"mcp__{config.server_name}__"
        ctx = _ctx(WorkingSource.MESSAGE_BUS, **extra)

        suppressed = await module.get_disallowed_tools(ctx)

        assert q + "message_agent" not in suppressed
        assert q + "message_team" not in suppressed
        assert suppressed == []


@pytest.mark.asyncio
async def test_the_reminder_still_defaults_to_the_turns_own_verb():
    """Reachability widened; the default did not.

    The reply reminder still names exactly the verb for the conversation that
    woke the turn, so the path of least resistance stays 'answer where you were
    spoken to'. Reaching another conversation costs a deliberate search + an
    explicit target — the intent gradient that replaces the old hard drop.
    """
    for extra, default in (
        ({}, "message_agent"),
        ({"bus_team_room": True}, "message_team"),
    ):
        module = _module()
        config = await module.get_mcp_config()
        q = f"mcp__{config.server_name}__"
        ctx = _ctx(WorkingSource.MESSAGE_BUS, **extra)

        assert await module.get_expressive_tools(ctx) == [q + default]


@pytest.mark.asyncio
async def test_a_patrol_turn_still_clears_both_send_verbs():
    """Patrol delivers by speaking — the platform posts its composed line and it
    calls no send tool — so both verbs come off the desk. This invariant is
    unchanged by the redesign and must not regress."""
    module = _module()
    config = await module.get_mcp_config()
    q = f"mcp__{config.server_name}__"
    ctx = _ctx(WorkingSource.MESSAGE_BUS, bus_plain_text_turn=True)

    suppressed = await module.get_disallowed_tools(ctx)

    assert q + "message_agent" in suppressed
    assert q + "message_team" in suppressed
