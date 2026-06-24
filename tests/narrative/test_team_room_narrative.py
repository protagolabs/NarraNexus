"""
@file_name: test_team_room_narrative.py
@author: NetMind.AI
@date: 2026-06-24
@description: Team group-chat (message bus) runs route to a dedicated per-room
narrative keyed under a room-scoped pseudo-user, so they never pollute the
agent's 1:1 narratives / chat history. See narrative/_narrative_impl/team_room.py.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.narrative.narrative_service import NarrativeService
from xyz_agent_context.narrative._narrative_impl.team_room import (
    TEAM_ROOM_SPECIAL,
    build_team_room_narrative_id,
    build_team_room_user_id,
)
from xyz_agent_context.repository import NarrativeRepository


def test_room_narrative_id_is_deterministic_and_per_agent():
    a = build_team_room_narrative_id("agent_1", "chan_x")
    assert a == build_team_room_narrative_id("agent_1", "chan_x")  # stable
    assert a.startswith("nar_room_")
    assert len(a) <= 128
    # Distinct per agent and per channel.
    assert a != build_team_room_narrative_id("agent_2", "chan_x")
    assert a != build_team_room_narrative_id("agent_1", "chan_y")


def test_room_user_id_is_not_the_owner():
    # The room user id must be a distinct namespace from any real user id.
    assert build_team_room_user_id("chan_x") == "room_chan_x"


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_and_isolated(db_client):
    agent_id = "agent_1"
    owner_user_id = "user_owner"
    channel_id = "chan_team_42"

    service = NarrativeService(agent_id)
    service._crud.set_database_client(db_client)

    first = await service.get_or_create_team_room_narrative(agent_id, channel_id)
    second = await service.get_or_create_team_room_narrative(agent_id, channel_id)

    # Same stable row, not a fresh one each call.
    assert first.id == second.id == build_team_room_narrative_id(agent_id, channel_id)
    assert first.is_special == TEAM_ROOM_SPECIAL
    assert first.env_variables.get("bus_channel_id") == channel_id

    # Exactly one narrative row persisted.
    rows = await db_client.get("narratives", filters={"agent_id": agent_id})
    assert len(rows) == 1

    room_user_id = build_team_room_user_id(channel_id)
    actor_ids = {a.id for a in first.narrative_info.actors}
    assert room_user_id in actor_ids
    # Isolation invariant: the OWNER is never an actor on the room narrative.
    assert owner_user_id not in actor_ids

    repo = NarrativeRepository(db_client)
    # The owner's 1:1 query (BM25 selection / chat-history fallback) must NOT
    # surface the room narrative...
    owner_narratives = await repo.get_by_agent_user(agent_id, owner_user_id)
    assert first.id not in {n.id for n in owner_narratives}
    # ...but it IS reachable under the room-scoped user.
    room_narratives = await repo.get_by_agent_user(agent_id, room_user_id)
    assert first.id in {n.id for n in room_narratives}
