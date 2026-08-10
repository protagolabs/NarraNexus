"""
@file_name: patrol.py
@author:
@date: 2026-08-10
@description: Leader patrol — the platform half of "somebody owns this flow".

The team room is a purely @-driven model: every member, the lead included,
only wakes when addressed. So a flow advances only while each hop remembers to
@ the next one, and any hop that forgets, goes quiet, or has its @ stripped by
the cascade cap kills the chain — with, structurally, nobody to notice. This
module is the periodic activation that gives the flow an owner.

What lives here is the part that must NOT be a model's opinion: deciding an
item is stalled, and deciding a team is due for a patrol. The lead's judgement
begins after that, and (owner decision 2026-08-07) is limited to chasing —
never re-assigning, because "idle with unfinished work" is not the same as
"this one is never coming back", and a wrong re-assignment means two agents
working the same deliverable.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, List, Optional, Tuple

from loguru import logger

from xyz_agent_context.message_bus._bus_activity import is_live
from xyz_agent_context.repository.team_work_repository import TeamWorkItemRepository
from xyz_agent_context.schema.team_work_schema import WorkItem, WorkItemStatus
from xyz_agent_context.utils.timezone import utc_now

#: Marks a patrol line in the transcript. The frontend renders it as the room
#: speaking rather than as the lead, and ``_team_cascade_depth`` skips it — a
#: patrol line is the platform taking stock, not an agent taking a turn.
PATROL_MSG_TYPE = "patrol"

#: How often a healthy board is looked at. Long on purpose — every patrol is a
#: real LLM turn, so the interval IS the cost.
PATROL_INTERVAL_S = 600

#: The pace once something is actually stuck. Shorter because this is the
#: window in which the flow is dead and nobody knows.
PATROL_STALLED_INTERVAL_S = 180


async def detect_stalled_items(db: Any, team_id: str) -> List[WorkItem]:
    """Items whose assignee has gone quiet. Writes ``stalled`` through.

    The evidence is ``bus_agent_activity``, never a model's read of the room
    (iron rule #15). Three shapes count as stalled, and one very deliberately
    does not:

    * assignee is ``idle`` while the item is unfinished — the Dunhuang shape:
      acknowledged, then silence;
    * assignee claims ``running`` but its heartbeat died — a wedged worker;
    * assignee has no activity row at all — never started;
    * **a fresh ``running`` heartbeat is NOT stalled, however long it has been
      going.** A 25-minute turn is work. Chasing it would make the platform
      the interruption source for exactly the long-running run iron rule #14
      protects.

    Unclaimed items are excluded: nobody is late on a task nobody took. That
    needs handing out, which is a different prompt.

    Recovery is written through too — an assignee that comes back leaves the
    stalled set, so patrol stops chasing someone already working again.
    """
    repo = TeamWorkItemRepository(db)
    items = await repo.list_active(team_id)
    stalled: List[WorkItem] = []
    for item in items:
        if not item.assignee_id:
            continue  # unclaimed: needs assigning, not chasing
        try:
            row = await db.get_one(
                "bus_agent_activity", {"agent_id": item.assignee_id}
            )
        except Exception as e:  # noqa: BLE001 — unreadable activity = unknown
            logger.debug(f"[patrol] activity lookup failed for {item.assignee_id}: {e}")
            continue
        working = is_live(row)
        if working:
            # Came back — clear a stall we may have recorded earlier.
            if item.status == WorkItemStatus.STALLED:
                await repo.set_status(item.item_id, WorkItemStatus.IN_PROGRESS)
            continue
        if item.status != WorkItemStatus.STALLED:
            await repo.set_status(item.item_id, WorkItemStatus.STALLED)
        item.status = WorkItemStatus.STALLED
        stalled.append(item)
    return stalled


def patrol_due_at(last_patrol_at: Optional[Any], has_stalled: bool) -> bool:
    """Whether a team's next patrol is due.

    Adaptive by design: a board where everything is running gets looked at
    rarely (minimum interference, minimum token burn), a board with something
    stuck gets looked at often (that is the window where the flow is dead and
    nobody knows).
    """
    if last_patrol_at is None:
        return True
    from xyz_agent_context.agent_runtime.run_recorder import parse_db_utc

    last = parse_db_utc(last_patrol_at)
    if last is None:
        return True
    interval = PATROL_STALLED_INTERVAL_S if has_stalled else PATROL_INTERVAL_S
    return (utc_now() - last) >= timedelta(seconds=interval)


async def teams_due_for_patrol(db: Any) -> List[Tuple[str, str, str]]:
    """``(team_id, lead_agent_id, channel_id)`` for every team to patrol now.

    The candidate query of the patrol lane, shaped like
    ``_agents_with_pending``: one pass for the whole fleet rather than asking
    each team in turn whether it has anything to do.

    Three gates, in cost order:

    1. the team has unfinished work — an empty board produces NO patrol runs at
       all, which is the feature's entire cost guarantee;
    2. the team has a lead and has not switched patrol off — the platform does
       not appoint someone in charge on the user's behalf;
    3. the interval has elapsed (adaptive, see ``patrol_due_at``).
    """
    repo = TeamWorkItemRepository(db)
    team_ids = await repo.teams_with_active_work()
    if not team_ids:
        return []

    due: List[Tuple[str, str, str]] = []
    for team_id in team_ids:
        try:
            team = await db.get_one("teams", {"team_id": team_id})
            if not team:
                continue
            lead = str(team.get("lead_agent_id") or "")
            if not lead:
                continue  # no lead = nobody responsible; we do not pick one
            # NULL = undecided, and reads as ON for a team that HAS a lead:
            # setting a lead IS the act of saying "this one is responsible".
            enabled = team.get("patrol_enabled")
            if enabled is not None and not enabled:
                continue
            items = await repo.list_active(team_id)
            if not items:
                continue
            has_stalled = any(i.status == WorkItemStatus.STALLED for i in items)
            if not patrol_due_at(team.get("last_patrol_at"), has_stalled):
                continue
            # Every active item of a team carries the same room.
            due.append((team_id, lead, items[0].channel_id))
        except Exception as e:  # noqa: BLE001 — one bad team never stops the sweep
            logger.warning(f"[patrol] candidate check failed for {team_id}: {e}")
    return due


async def mark_patrolled(db: Any, team_id: str) -> None:
    """Stamp the patrol cursor. Called however the patrol turn ended.

    Stamped on EXIT rather than on success: a patrol that crashed still
    consumed its slot, and re-running it immediately would turn one broken
    team into a hot loop.
    """
    try:
        await db.update("teams", {"team_id": team_id}, {"last_patrol_at": utc_now()})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[patrol] could not stamp cursor for {team_id}: {e}")


# --- Patrol's own speech limit ---------------------------------------------
#
# Patrol messages are exempt from the cascade-depth cap (owner decision
# 2026-08-07, option a), which is what makes a chase @ actually reach its
# target in the one situation that matters: a dead flow IS a long unbroken run
# of agent messages, so depth is already at the cap and the @ would otherwise
# be stripped without a trace.
#
# The exemption removes patrol from the runaway-@ protection, so this counter
# is the only backstop left — and it lives on DISK. The bus's existing limiter
# (`MessageBusTrigger._rate_counters`) is an in-memory dict keyed on
# `time.monotonic()`; it is fine for damping a chatty agent and useless here,
# because a workers restart would hand a confused model a fresh budget.

#: Patrol messages allowed per team per window.
PATROL_SPEECH_MAX = 6

#: The window, in seconds.
PATROL_SPEECH_WINDOW_S = 1800


async def may_patrol_speak(db: Any, team_id: str) -> bool:
    """Whether patrol may post into this team's room right now.

    Fails OPEN. Patrol's job is telling the owner something is wrong, so
    silencing it because its own bookkeeping row could not be read would drop
    the message in the very case it matters most. A wrong "allow" costs one
    extra line in a room; a wrong "deny" costs a flow that dies unannounced.
    """
    try:
        row = await db.get_one("teams", {"team_id": team_id})
        if not row:
            return True
        if not _within_speech_window(row.get("patrol_spoke_at")):
            return True  # window rolled over — the count is stale
        return int(row.get("patrol_spoke_count") or 0) < PATROL_SPEECH_MAX
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[patrol] speech-limit check failed for {team_id}: {e}")
        return True


async def note_patrol_spoke(db: Any, team_id: str) -> None:
    """Record one patrol message. Starts a new window when the old one lapsed."""
    try:
        row = await db.get_one("teams", {"team_id": team_id})
        if not row:
            return
        count = (
            int(row.get("patrol_spoke_count") or 0) + 1
            if _within_speech_window(row.get("patrol_spoke_at"))
            else 1
        )
        await db.update(
            "teams",
            {"team_id": team_id},
            {"patrol_spoke_at": utc_now(), "patrol_spoke_count": count},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[patrol] could not record speech for {team_id}: {e}")


def _within_speech_window(spoke_at: Optional[Any]) -> bool:
    if spoke_at is None:
        return False
    from xyz_agent_context.agent_runtime.run_recorder import parse_db_utc

    last = parse_db_utc(spoke_at)
    if last is None:
        return False
    return (utc_now() - last) < timedelta(seconds=PATROL_SPEECH_WINDOW_S)
