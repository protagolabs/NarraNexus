"""
@file_name: team_rooms.py
@author:
@date: 2026-08-17
@description: Provisioning a team's room — the one implementation.

A team room IS a bus group channel whose ``created_by`` is the synthetic
``team_<id>`` marker. That marker is load-bearing: it is a NON-AGENT value, so
``MessageBusTrigger._should_process_message``'s "the channel owner is always
activated" rule can never match a member, and delivery in a team room stays
purely @-mention driven.

This lived inline in ``backend/routes/teams.py``. It moved here when agents
gained ``create_team``: a core-package MCP tool cannot import from ``backend``
without inverting the layering, and copying the provisioning would have given
the codebase two implementations of the marker convention — the exact shape
that keeps producing drift elsewhere in this subsystem.
"""
from __future__ import annotations

from typing import Any, Sequence

from loguru import logger

from xyz_agent_context.schema.team_schema import TEAM_ROOM_OWNER_PREFIX


def team_room_marker(team_id: str) -> str:
    """The synthetic ``created_by`` that identifies a team's room."""
    return f"{TEAM_ROOM_OWNER_PREFIX}{team_id}"


async def get_or_create_team_room(
    db: Any,
    bus: Any,
    *,
    team_id: str,
    team_name: str,
    member_agent_ids: Sequence[str],
) -> str:
    """Find (or create) the team's room and sync its members. Returns channel_id.

    Membership is synced on every call because ``team_members`` is the source of
    truth and ``bus_channel_members`` is what delivery actually reads. The two
    drift the moment a member is added or removed, and this is where they are
    reconciled.
    """
    marker = team_room_marker(team_id)
    existing = await db.get_one(
        "bus_channels", {"created_by": marker, "channel_type": "group"}
    )
    if existing:
        channel_id = existing["channel_id"]
    else:
        # `create_channel` sets created_by = members[0]; rewrite it immediately
        # to the non-agent marker so no member becomes the always-activated
        # channel owner.
        channel_id = await bus.create_channel(
            name=team_name or "Team",
            members=list(member_agent_ids),
            channel_type="group",
        )
        await db.update("bus_channels", {"channel_id": channel_id}, {"created_by": marker})
        logger.info(f"[team-room] provisioned {channel_id} for {team_id}")

    current = {m.agent_id for m in await bus.get_channel_members(channel_id)}
    target = set(member_agent_ids)
    for aid in target - current:
        await bus.join_channel(aid, channel_id)
    for aid in current - target:
        await bus.leave_channel(aid, channel_id)

    return channel_id


async def resolve_team_room(db: Any, team_id: str) -> str:
    """The team's channel_id, or "" when the room has never been opened.

    A read-only counterpart for callers that must not create anything — posting
    a notice into a team that has no room yet would be the tail wagging the dog.
    """
    row = await db.get_one(
        "bus_channels",
        {"created_by": team_room_marker(team_id), "channel_type": "group"},
    )
    return (row or {}).get("channel_id", "") or ""


async def room_roster(db: Any, bus: Any, channel_id: str) -> list[dict]:
    """``[{"agent_id", "name"}]`` for everyone in the room.

    Names come from the `agents` table in one batched read, not a lookup per
    member: a roster is a few dozen rows at most and N+1 here would sit on the
    delivery path.

    Used for @mention resolution — a mention is written as a NAME and has to
    become an id — so a member whose row is missing still appears, keyed by id.
    Dropping them would silently make that teammate unmentionable.
    """
    members = await bus.get_channel_members(channel_id)
    ids = [m.agent_id for m in members]
    if not ids:
        return []
    rows = await db.get_by_ids("agents", "agent_id", ids) or []
    names = {r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in rows if r}
    return [{"agent_id": aid, "name": names.get(aid, aid)} for aid in ids]


__all__ = [
    "team_room_marker",
    "get_or_create_team_room",
    "resolve_team_room",
    "room_roster",
]
