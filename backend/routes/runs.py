"""
@file_name: runs.py
@author:
@date: 2026-08-07
@description: Run-level control plane — today: the owner's stop request.

Why a run-scoped route rather than a team-scoped one: run observability is
already a PLATFORM property (#219 — one recorder for every trigger run, one
observation endpoint for every reader). Stopping is the write-side twin of
that, so it keys off run_id and stays usable by any surface that can name a
run (team roster today, a runs dashboard later), not just team rooms.

This route only RECORDS intent. The run lives in another process (workers),
so the actual interruption is delivered there by CancelWatcher reading
``events.cancel_requested_at``. Recording and delivering are deliberately
separate: the click gets its answer in one round trip regardless of how
loaded the workers process is, which is the whole point of the feature —
the incident that motivated it was 8 minutes of silence, not a slow stop.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from backend.auth import resolve_current_user_id
from xyz_agent_context.agent_runtime.run_recorder import STATE_RUNNING
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now

router = APIRouter(tags=["runs"])


class CancelRunResponse(BaseModel):
    success: bool
    run_id: str
    state: str
    already_settled: bool
    # How many runs the request was applied to, this one included. >1 means the
    # run had caused others (an agent asking a peer, that peer asking a third)
    # and the whole tree was stopped.
    cascaded: int = 1


# A team room's channel is owned by the synthetic ``team_<id>`` marker rather
# than by any agent — same convention as backend/routes/teams.py.
TEAM_ROOM_OWNER_PREFIX = "team_"

# Marks the stop notice in the transcript. The frontend renders it from an i18n
# key (the DB cannot know the reader's language); ``content`` carries an English
# fallback for consumers that only read text, e.g. the memory index.
STOP_NOTICE_MSG_TYPE = "system_stop"


async def _leave_room_trace(db, run_id: str, agent_id: str) -> None:
    """Post "this agent was stopped" into the team room, if the run was in one.

    A team task runs in public, so it should stop in public: without a trace the
    other members (and the owner, later) see a task that simply vanished and are
    left guessing whether it finished, crashed, or is still going.

    Best-effort by design — the stop itself is already durable, and failing to
    narrate it must never turn a successful stop into a 500.
    """
    try:
        activity = await db.get_one("bus_agent_activity", {"event_id": run_id})
        channel_id = (activity or {}).get("channel_id") or ""
        if not channel_id:
            return  # not a bus/room run (chat, job) — nothing to narrate

        channel = await db.get_one("bus_channels", {"channel_id": channel_id})
        if not str((channel or {}).get("created_by") or "").startswith(
            TEAM_ROOM_OWNER_PREFIX
        ):
            return  # a peer DM is not an audience

        from xyz_agent_context.message_bus.local_bus import LocalMessageBus

        bus = LocalMessageBus(backend=db._backend)
        await bus.send_message(
            from_agent=agent_id,
            to_channel=channel_id,
            content="Run stopped by owner.",
            msg_type=STOP_NOTICE_MSG_TYPE,
            # No mentions on purpose: a group message without mentions activates
            # nobody (a team room's channel owner is the `team_` marker, never an
            # agent), so the notice cannot wake the very agents just stopped.
            mentions=None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[run-cancel] could not post stop notice for {run_id}: {e}")


@router.post("/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run(run_id: str, request: Request) -> CancelRunResponse:
    """Ask the run to stop. Owner only.

    Returns as soon as the request is durable — the caller is expected to
    render "stopping" off this response and watch the run's observation
    stream for the terminal state, rather than waiting here.

    Raises:
        HTTPException: 404 when the run is unknown, 403 when the caller does
            not own the agent.
    """
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()

    row = await db.get_one("events", {"event_id": run_id})
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")

    # Ownership is the agent's owner — NOT events.user_id, which stores the
    # run's triggering key (in a team room: the sender). Resolving it here
    # rather than trusting the row is what keeps a room participant from
    # stopping somebody else's agent. See AgentRepository.resolve_owner.
    owner = await AgentRepository(db).resolve_owner(row.get("agent_id", ""))
    if not owner or owner != user_id:
        # 403 regardless of what the frontend rendered — the button's
        # visibility is a hint, this is the boundary.
        raise HTTPException(status_code=403, detail="Only the agent's owner can stop a run")

    state = row.get("state") or ""
    if state != STATE_RUNNING:
        # Nothing to stop. Crucially we do NOT stamp the flag: it would sit on
        # a terminal row as a trap for the agent's next run, whose watcher
        # compares the flag against its own started_at.
        return CancelRunResponse(success=True, run_id=run_id, state=state, already_settled=True)

    # Stop the whole TREE, not just this run. An agent working on a long task
    # routinely asks a peer, which asks a third — stopping only the run that was
    # clicked leaves those branches burning tokens, and worse, their queued
    # messages would wake NEW runs: the owner presses stop and watches fresh
    # work appear (the "whack-a-mole" the design calls out).
    #
    # The tree is a flat inherited label, so one indexed UPDATE covers any depth
    # — no recursion, no walking. `root_run_id` is NULL on runs that predate the
    # column; those fall back to stopping just this row rather than matching
    # every other NULL row in the table.
    requested_at = utc_now()
    root = row.get("root_run_id") or ""
    if root:
        siblings = await db.get("events", {"root_run_id": root, "state": STATE_RUNNING})
    else:
        siblings = [row]

    cascaded = 0
    for sibling in siblings or []:
        # Repeated clicks keep each row's ORIGINAL timestamp: the watcher's
        # verdict is `requested >= started_at`, and re-stamping later could move
        # that verdict for a run that started in between.
        if sibling.get("cancel_requested_at"):
            cascaded += 1
            continue
        try:
            await db.update(
                "events",
                {"event_id": sibling["event_id"]},
                {"cancel_requested_at": requested_at},
            )
            cascaded += 1
        except Exception as e:  # noqa: BLE001
            # One unwritable row must not abort the rest of the tree — a
            # partially stopped tree still beats a fully running one, and the
            # caller learns the real count.
            logger.warning(
                f"[run-cancel] could not flag {sibling.get('event_id')!r}: {e}"
            )

    logger.info(
        f"[run-cancel] stop requested for run {run_id} by {user_id} "
        f"(tree={root or 'single'}, runs={cascaded})"
    )

    await _leave_room_trace(db, run_id, row.get("agent_id", ""))

    return CancelRunResponse(
        success=True,
        run_id=run_id,
        state=state,
        already_settled=False,
        cascaded=cascaded,
    )
