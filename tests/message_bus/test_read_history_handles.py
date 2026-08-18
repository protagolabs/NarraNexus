"""
@file_name: test_read_history_handles.py
@author:
@date: 2026-08-18
@description: `read_history` takes conversation HANDLES, not channel ids.

The redesign's premise is that an agent's world holds private conversations and
teams — not channels. `read_history(agent_id, channel_id, limit)` was the one
tool that contradicted it, and the contradiction was load-bearing: to call it,
the agent needed an id, so the instruction printed a `### Your Channels` list of
raw `channel_id`s and `channel_type`s into every turn. Removing the list without
changing the tool would have left the tool uncallable; changing the tool is what
let the list go.

The membership assertions are the ones that matter. Both lookups are written so
the AUTHORISATION IS THE QUERY — the DM join requires the caller to be one of
the two members, the team branch requires a `team_members` row — rather than
"find the channel, then check". The second shape is one forgotten branch away
from an agent reading a conversation it is not in, and that branch is exactly
the kind that gets added while fixing something else.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.message_bus_module._message_bus_mcp_tools import (
    _resolve_conversation,
)

ME, PEER, STRANGER = "agent_me", "agent_peer", "agent_stranger"
TEAM, ROOM = "team_1", "ch_team_1"


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )


async def _dm(db, a, b, channel_id):
    await db.insert(
        "bus_channels",
        {"channel_id": channel_id, "name": f"dm_{a}_{b}",
         "channel_type": "direct", "created_by": a},
    )
    for aid in (a, b):
        await db.insert(
            "bus_channel_members", {"channel_id": channel_id, "agent_id": aid}
        )


@pytest.mark.asyncio
async def test_a_private_conversation_resolves_by_peer(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _dm(db_client, ME, PEER, "ch_dm_1")

    channel_id, err = await _resolve_conversation(ME, with_agent=PEER, team_id="")
    assert err is None
    assert channel_id == "ch_dm_1"


@pytest.mark.asyncio
async def test_a_conversation_i_am_not_in_is_not_found(db_client, monkeypatch):
    """Not "forbidden" — not FOUND. The join is the authorisation.

    A DM between two other agents exists and is perfectly readable by them; the
    query simply cannot return it to a third party, because the caller's own id
    is one of the two things it joins on.
    """
    _patch_db(monkeypatch, db_client)
    await _dm(db_client, PEER, STRANGER, "ch_dm_theirs")

    channel_id, err = await _resolve_conversation(ME, with_agent=STRANGER, team_id="")
    assert channel_id is None
    assert err and STRANGER in err


@pytest.mark.asyncio
async def test_a_team_resolves_to_its_room_for_a_member(db_client, monkeypatch):
    from xyz_agent_context.message_bus.team_rooms import team_room_marker

    _patch_db(monkeypatch, db_client)
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": ME})
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": ROOM,
            "name": "room",
            "channel_type": "group",
            "created_by": team_room_marker(TEAM),
        },
    )

    channel_id, err = await _resolve_conversation(ME, with_agent="", team_id=TEAM)
    assert err is None
    assert channel_id == ROOM


@pytest.mark.asyncio
async def test_a_team_i_am_not_in_is_refused_before_the_room_is_looked_up(
    db_client, monkeypatch
):
    """The room is public-ish (its channel row is findable by team id); the
    membership row is what gates it, and it is checked first."""
    from xyz_agent_context.message_bus.team_rooms import team_room_marker

    _patch_db(monkeypatch, db_client)
    await db_client.insert("team_members", {"team_id": TEAM, "agent_id": PEER})
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": ROOM,
            "name": "room",
            "channel_type": "group",
            "created_by": team_room_marker(TEAM),
        },
    )

    channel_id, err = await _resolve_conversation(ME, with_agent="", team_id=TEAM)
    assert channel_id is None
    assert err and "not in team" in err


@pytest.mark.asyncio
async def test_nothing_there_yet_is_an_answer_not_an_exception(db_client, monkeypatch):
    """Both "no such conversation" shapes come back as `(None, message)`.

    The tool turns these into `{"success": false, "error": ...}`; an exception
    here would surface to the agent as a stack-trace string it cannot act on.
    """
    _patch_db(monkeypatch, db_client)

    assert (await _resolve_conversation(ME, with_agent=PEER, team_id=""))[0] is None
    assert (await _resolve_conversation(ME, with_agent="", team_id=TEAM))[0] is None
