"""
@file_name: team_posting.py
@author:
@date: 2026-08-17
@description: Putting an agent's words into a team room — the one path.

A team reply used to be the agent's PLAIN TEXT, auto-posted by
``MessageBusTrigger._deliver_reply``. That made the team room the only surface
in the system where "plain text reaches nobody" was false, and that exception
propagated: the framework constitution, ChatModule's instructions and the bus
module's rules all assert the general rule, and only one of the three had a way
to be switched off per turn. Six review rounds on PR #311 were spent on the
contradictions that grew out of it.

So the room now takes a tool call like every other surface, and this module is
what that tool calls. Everything ``_deliver_reply`` did lives here — @mention
resolution, the agent-to-agent hop cap, the turn stamp — because those are
properties of POSTING INTO A ROOM, not of the trigger that happened to own the
old delivery path.

The cap moving here is the fix for a specific hole: it was implemented only in
``_deliver_reply``, while ``bus_send_message`` could write into a team room with
no hop counting at all. The loop-breaker was installed on the door the agent was
told not to use.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from xyz_agent_context.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    placeholders as _platform_placeholders,
)
from xyz_agent_context.schema.team_schema import USER_SENDER_PREFIX

#: How many consecutive agent hops may pass with no human message before
#: @mentions stop being delivered. A dead flow IS a long unbroken run of agent
#: messages, so this is the number that decides when the room stops relaying.
MAX_TEAM_AGENT_HOPS = 4


def extract_team_mentions(text: str, member_map: Dict[str, str]) -> List[str]:
    """Resolve @mentions in a reply to channel-member agent ids.

    Returns EITHER ``["@everyone"]`` OR a list of ids, never a mix — callers
    downstream (the cap notice in particular) depend on that shape.
    """
    tokens = {t.lower() for t in re.findall(r"@([\w一-鿿]+)", text or "")}
    if not tokens:
        return []
    if "all" in tokens or "everyone" in tokens:
        return ["@everyone"]
    out: List[str] = []
    for aid, name in member_map.items():
        nm = (name or aid).lower()
        first = nm.split()[0] if nm.split() else nm
        if nm in tokens or first in tokens or any(
            len(t) >= 2 and nm.startswith(t) for t in tokens
        ):
            out.append(aid)
    return out


async def team_cascade_depth(db: Any, channel_id: str) -> int:
    """How many consecutive agent (non-user) messages end this channel.

    A user message resets it. Platform lines are excluded IN SQL, not skipped
    afterwards: the window is a fixed LIMIT, so a skipped row still consumes a
    slot — with three patrol lines among the last six messages only three
    countable hops fit, ``depth`` could never reach the cap, and the runaway-@
    guard silently stopped applying in exactly the rooms patrol frequents.
    """
    # `%s` and the CLIENT, not the raw backend's own placeholder.
    #
    # This query came out of `MessageBusTrigger`, where `self._bus._db` IS the raw
    # backend and takes SQL verbatim. Every caller here holds an
    # `AsyncDatabaseClient` instead — which has no `.placeholder` at all, so
    # reading one raised `AttributeError` inside the cap check and took the whole
    # send down with it: the hop cap turned every `message_team` call into
    # `{"success": false}`. The client translates `%s` per dialect, which is what
    # the dual-dialect contract wants anyway.
    rows = await db.execute(
        f"SELECT from_agent FROM bus_messages WHERE channel_id = %s "
        f"AND (msg_type IS NULL OR msg_type NOT IN ({_platform_placeholders()})) "
        f"ORDER BY created_at DESC LIMIT {MAX_TEAM_AGENT_HOPS + 2}",
        (channel_id, *PLATFORM_MSG_TYPES),
        fetch=True,
    )
    depth = 0
    for r in rows or []:
        if str(r["from_agent"]).startswith(USER_SENDER_PREFIX):
            break
        depth += 1
    return depth


async def post_team_reply(
    *,
    db: Any,
    bus: Any,
    agent_id: str,
    team_id: str,
    channel_id: str,
    text: str,
    roster: Sequence[dict],
    event_id: Optional[str] = None,
    root_run_id: str = "",
) -> dict:
    """Post one agent message into a team room, applying the room's rules.

    Returns ``{"message_id", "mentioned", "capped"}``. ``capped`` names who was
    NOT pulled in when the hop cap fired, so the caller can narrate it — a
    platform that silently declines to relay trains the room to read it as the
    teammate ignoring the request.
    """
    member_map = {r["agent_id"]: r.get("name") or r["agent_id"] for r in roster}
    mentions = extract_team_mentions(text, member_map)
    capped: dict = {"names": [], "everyone": False}

    if mentions:
        depth = await team_cascade_depth(db, channel_id)
        if depth >= MAX_TEAM_AGENT_HOPS:
            logger.info(
                f"[team-post] cascade depth {depth} >= {MAX_TEAM_AGENT_HOPS} "
                f"in {channel_id}; dropping @mentions to break the loop"
            )
            capped["everyone"] = "@everyone" in mentions
            capped["names"] = [
                member_map.get(m, m) for m in mentions if m != "@everyone"
            ]
            mentions = []

    message_id = await bus.send_message(
        from_agent=agent_id,
        to_channel=channel_id,
        content=text,
        mentions=mentions or None,
        event_id=event_id,
        root_run_id=root_run_id,
    )

    if capped["names"] or capped["everyone"]:
        # Best-effort: the reply is already in the room and failing to narrate
        # the cap must not undo that.
        try:
            from xyz_agent_context.message_bus.team_notices import post_cascade_capped

            await post_cascade_capped(
                db,
                team_id=team_id,
                channel_id=channel_id,
                dropped=capped["names"],
                depth=MAX_TEAM_AGENT_HOPS,
                dropped_everyone=capped["everyone"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[team-post] could not narrate the cap: {e}")

    return {
        "message_id": message_id,
        "mentioned": mentions,
        "capped": capped,
    }


__all__ = [
    "MAX_TEAM_AGENT_HOPS",
    "extract_team_mentions",
    "team_cascade_depth",
    "post_team_reply",
]
