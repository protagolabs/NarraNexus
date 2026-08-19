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
    """The invariant the whole redesign rests on.

    Two send verbs on one turn is a choice the model should never have had, and
    the wrong branch posts into the wrong conversation.
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


# ── the desk: declaration ∪ suppression ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_desk_holds_exactly_the_verb_for_this_turn():
    """Declaration and suppression have to agree, or the invariant is a slogan.

    Declaring one verb while both schemas stay in the model's context is how a
    rule ends up arguing with a tool the agent can still see — and prose loses
    that argument: 615 prod calls landed on two tools whose docstrings said "Do
    NOT call".

    **Called in the runtime's order, on a fresh instance, deliberately.**
    `context_runtime` asks for suppression BEFORE declaration. An earlier draft
    read the turn from state the declaration left on the instance, and this test
    called declaration first — so it passed while every team turn shipped with
    `message_team` declared AND suppressed, i.e. a room nobody could speak in.
    A guard that calls the two hooks in the reverse of production order certifies
    the bug it exists to catch.
    """
    for extra, kept, dropped in (
        ({}, "message_agent", "message_team"),
        ({"bus_team_room": True}, "message_team", "message_agent"),
    ):
        module = _module()  # fresh per case: no state may carry between turns
        config = await module.get_mcp_config()
        q = f"mcp__{config.server_name}__"
        ctx = _ctx(WorkingSource.MESSAGE_BUS, **extra)

        suppressed = await module.get_disallowed_tools(ctx)
        declared = await module.get_expressive_tools(ctx)

        assert declared == [q + kept]
        assert suppressed == [q + dropped]
        assert q + kept not in suppressed, "the turn's own verb was taken away"
        assert q + dropped not in declared


@pytest.mark.asyncio
async def test_suppression_reads_the_turn_it_is_given():
    """One instance, two turns of different kinds, suppression asked first each
    time — the shape a long-lived module instance actually sees."""
    module = _module()
    config = await module.get_mcp_config()
    q = f"mcp__{config.server_name}__"

    team = _ctx(WorkingSource.MESSAGE_BUS, bus_team_room=True)
    assert await module.get_disallowed_tools(team) == [q + "message_agent"]

    peer = _ctx(WorkingSource.MESSAGE_BUS)
    assert await module.get_disallowed_tools(peer) == [q + "message_team"]

    # And back, so a stale answer cannot pass by matching the last turn asked.
    assert await module.get_disallowed_tools(team) == [q + "message_agent"]
