"""
@file_name: test_create_team_tool.py
@date: 2026-08-19
@description: `create_team` builds a REAL team, not an orphan bus channel.

The tool sits in the byte-stable instruction block, so the model calls it every
bus turn. Before this test it just renamed `bus_create_channel`: no `teams` row,
no `team_members` row, `created_by = the creator agent` (so the creator became
the always-activated channel owner), and it returned a `channel_id` the agent
had no verb to use — `message_team` looks the team up in `teams` and got None.

The load-bearing assertion is the end-to-end one: create_team -> message_team
must SUCCEED. Revert create_team to `bus.create_channel(...)` and it goes red on
"team not found for this owner".
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.team_rooms import team_room_marker
from xyz_agent_context.module.message_bus_module._message_bus_mcp_tools import (
    register_message_bus_mcp_tools,
)

OWNER, OTHER_OWNER = "usr_owner", "usr_other"
ME, PEER, STRANGER = "agent_me", "agent_peer", "agent_stranger"


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )


async def _agent(db, agent_id, owner):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": owner})


def _tools(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    captured: dict = {}

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                captured[fn.__name__] = fn
                return fn
            return _wrap

    async def _bus():
        return bus

    register_message_bus_mcp_tools(_Stub(), _bus)
    return captured


@pytest.mark.asyncio
async def test_create_team_makes_a_real_team_the_agent_can_then_message(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    await _agent(db_client, PEER, OWNER)
    tools = _tools(db_client)

    created = await tools["create_team"](
        agent_id=ME, name="Project Alpha Coordination", members=PEER
    )
    assert created["success"] is True, created
    team_id = created["team_id"]

    # A real `teams` row owned by the creator's user — so it shows in the UI and
    # passes message_team's owner check.
    team = await db_client.get_one("teams", {"team_id": team_id})
    assert team is not None and team["owner_user_id"] == OWNER

    # `team_members` rows for both — the membership message_team/read_history gate.
    members = {
        m["agent_id"]
        for m in await db_client.get("team_members", {"team_id": team_id})
    }
    assert members == {ME, PEER}

    # The room's created_by is the NON-AGENT marker, not the creator — otherwise
    # the creator would be the always-activated channel owner.
    room = await db_client.get_one(
        "bus_channels", {"created_by": team_room_marker(team_id), "channel_type": "group"}
    )
    assert room is not None, "no team room with the team_<id> marker as created_by"

    # THE point: the agent can actually talk in the team it just created.
    posted = await tools["message_team"](agent_id=ME, team_id=team_id, text="kickoff")
    assert posted["success"] is True, posted


@pytest.mark.asyncio
async def test_create_team_rejects_a_cross_owner_invite_without_writing_a_team(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    await _agent(db_client, STRANGER, OTHER_OWNER)  # different owner
    tools = _tools(db_client)

    result = await tools["create_team"](agent_id=ME, name="Cross Tenant", members=STRANGER)
    assert result["success"] is False and "cross-user" in result["error"]

    # And it left NO orphan team row behind (checked before any write).
    assert await db_client.get("teams", {"owner_user_id": OWNER}) == []


@pytest.mark.asyncio
async def test_create_team_rolls_back_the_team_row_if_the_room_build_crashes(db_client, monkeypatch):
    """A crash BETWEEN writing the teams row and building its room must not leave
    an orphan team (message_team would find the team but no room). The row is
    rolled back so a retry is clean."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    await _agent(db_client, PEER, OWNER)
    tools = _tools(db_client)

    async def _boom(*_a, **_k):
        raise RuntimeError("room build failed")

    monkeypatch.setattr(
        "xyz_agent_context.message_bus.team_rooms.get_or_create_team_room", _boom
    )

    result = await tools["create_team"](agent_id=ME, name="Doomed", members=PEER)
    assert result["success"] is False

    # No orphan: neither the teams row nor its team_members survive.
    assert await db_client.get("teams", {"owner_user_id": OWNER}) == []
    assert await db_client.get("team_members", {"agent_id": ME}) == []
