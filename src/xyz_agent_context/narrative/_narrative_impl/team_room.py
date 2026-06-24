"""
@file_name: team_room.py
@author: NetMind.AI
@date: 2026-06-24
@description: Dedicated per-room Narrative for team group chat (message bus).

A team group-chat room is a single message-bus group channel (see
``teams.py`` / ``message_bus_trigger.py``). When an agent replies in such a
room its run MUST NOT write events / chat memory into the agent's regular 1:1
narratives — otherwise the group chat pollutes the owner's 1:1 chat history,
sidebar preview and BM25 routing.

The cure is identity isolation: each (agent, channel) pair gets ONE stable
narrative, keyed under a room-scoped pseudo-user (``room_<channel_id>``) that
is deliberately NOT the agent owner. Every owner-keyed 1:1 surface
(``chat-history`` / ``simple-chat-history`` / ``get_by_agent_user`` BM25
selection) filters by the owner's id and therefore never sees this narrative
or its ChatModule instance. The team run forces this narrative via
``forced_narrative_id`` (see ``message_bus_trigger`` + ``step_1``).

This builder mirrors ``default_narratives.create_default_narrative``: it
returns an unsaved ``Narrative`` (with ``is_special="team_room"``); the
ChatModule instance is provisioned lazily by ``step_1`` the first time the
room narrative is used.
"""

from __future__ import annotations

import hashlib

from xyz_agent_context.utils import utc_now

from ..models import (
    Narrative,
    NarrativeActor,
    NarrativeActorType,
    NarrativeInfo,
    NarrativeType,
)

# is_special sentinel marking a team group-chat room narrative. Kept distinct
# from "default" / "other" so the category is explicit and greppable.
TEAM_ROOM_SPECIAL = "team_room"


def build_team_room_narrative_id(agent_id: str, channel_id: str) -> str:
    """Deterministic per-(agent, channel) narrative id for a team room.

    Stable so every bus turn for the same agent in the same channel routes to
    the SAME narrative (hence the same room-scoped chat memory) instead of BM25
    re-selecting / re-creating one each turn. The agent_id is part of the hash
    because narratives are per-agent — two agents in one channel must get two
    distinct narratives. sha1[:24] keeps it well within the VARCHAR(128) column.
    """
    digest = hashlib.sha1(f"{agent_id}:{channel_id}".encode("utf-8")).hexdigest()[:24]
    return f"nar_room_{digest}"


def build_team_room_user_id(channel_id: str) -> str:
    """Room-scoped pseudo-user id under which the room narrative + its
    ChatModule instance are keyed.

    Deliberately NOT the agent owner: owner-keyed 1:1 read surfaces never match
    it, so group chat stays out of 1:1 chat history / sidebar / BM25 routing.
    """
    return f"room_{channel_id}"


def create_team_room_narrative(agent_id: str, channel_id: str) -> Narrative:
    """Build (but do not persist) the team-room narrative for (agent, channel)."""
    now = utc_now()
    room_user_id = build_team_room_user_id(channel_id)

    narrative_info = NarrativeInfo(
        name=f"Team room {channel_id}",
        description="Team group-chat room (message bus)",
        current_summary="Team group-chat room",
        # Room-scoped user actor, NOT the owner — this is what isolates the
        # narrative from every owner-keyed 1:1 surface.
        actors=[
            NarrativeActor(id=agent_id, type=NarrativeActorType.AGENT),
            NarrativeActor(id=room_user_id, type=NarrativeActorType.USER),
        ],
    )

    return Narrative(
        id=build_team_room_narrative_id(agent_id, channel_id),
        type=NarrativeType.CHAT,
        agent_id=agent_id,
        narrative_info=narrative_info,
        main_chat_instance_id=None,
        event_ids=[],
        env_variables={"bus_channel_id": channel_id, "room_user_id": room_user_id},
        is_special=TEAM_ROOM_SPECIAL,
        created_at=now,
        updated_at=now,
    )
