"""
@file_name: team_rooms.py
@author:
@date: 2026-08-17
@description: One answer to "where is this team's room".

"A team room is the group channel whose ``created_by`` is the ``team_<id>``
marker" is a convention, not a law, and it had grown four independent
implementations — the work-board tools, the bulletin notifier, the teams route,
and (newest) the job origin resolver. Four copies of a convention is three
chances for them to disagree the day it changes: give a team a second room, or
add an ``is_primary`` flag, and whoever misses a copy ships a feature that
resolves to a different room than the rest of the product.

The newest copy had already drifted before it landed, which is what prompted
this: it dropped the ``bus_agent_activity`` fallback its sibling carries, so the
same team could resolve one way for the board tools and another for jobs.

## What is NOT folded in here

``backend/routes/teams.py`` resolves rooms for MANY teams in ONE query
(``created_by IN (...)``). It looks like a fifth copy and is not: it answers a
different question, and rewriting it in terms of this helper would turn one
indexed query into N — the N+1 shape this repository's repository layer exists
to avoid. It shares the CONVENTION, so it is listed in the docstring below; it
does not share the code.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.schema.team_schema import TEAM_ROOM_OWNER_PREFIX


async def primary_room_of(db: Any, team_id: str) -> Optional[str]:
    """The channel id of ``team_id``'s room, or None.

    The single-team form of the convention. Callers that need it for several
    teams at once should keep their batched query (see the module docstring)
    but must stay consistent with the rule expressed here.

    None means "no room", never an empty string: every caller uses the result
    as a write target, and half an answer would send a row to ``""``. A team
    whose room does not exist yet is a normal state, not an error — the room is
    created lazily.

    Never raises: the three callers all treat "no room" as a reason to decline
    quietly (no board, no bulletin notice, no job origin), and none of them can
    do anything useful with an exception.
    """
    if not team_id:
        return None
    try:
        row = await db.get_one(
            "bus_channels",
            {
                "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
                "channel_type": "group",
            },
        )
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"[team-rooms] lookup failed for {team_id}: {e}")
        return None
    return (row or {}).get("channel_id") or None
