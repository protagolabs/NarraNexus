"""
@file_name: _work_board_mcp_tools.py
@author:
@date: 2026-08-07
@description: MCP tools for the team work board.

A separate file from ``_message_bus_mcp_tools.py`` even though both register
onto the same module's MCP server: the board is a task-level object with its
own state machine and its own platform/model boundary, and mixing it into the
messaging tools would bury that boundary in a 500-line file.

The boundary is the point of this file. A model may open, claim and finish
items. It may NOT write:

  * ``stalled`` — derived by the platform from ``bus_agent_activity`` and
    errand timeouts. Iron rule #15: a correctness-critical fact must not
    depend on model obedience. If a model could assert it, the patrol prompt's
    "these are stalled" section would just be reporting the model's own guess
    back to it.
  * ``paused`` — what a stop leaves behind. An agent that could pause its own
    board could silence patrol, i.e. switch off the supervision this whole
    feature exists to add.
  * ``cancelled`` — the user's call, not an agent's.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from loguru import logger

from xyz_agent_context.module._mcp_identity import caller_root_run_id
from xyz_agent_context.schema.team_work_schema import WorkItemStatus

# The room prefix convention, same as backend/routes/teams.py and the trigger.
TEAM_ROOM_OWNER_PREFIX = "team_"


async def _get_db():
    from xyz_agent_context.utils.db.db_factory import get_db_client

    return await get_db_client()


async def _resolve_team_room(db, agent_id: str) -> Tuple[Optional[str], Optional[str]]:
    """``(team_id, channel_id)`` for the room this agent is currently working in.

    Derived from ``bus_agent_activity`` rather than from an injected header:
    that mirror row is written ONLY by the trigger's team branch, so "has a
    live activity row in a team-owned channel" is exactly the condition under
    which a work board exists. A peer DM or an owner-chat turn resolves to
    ``(None, None)`` and the tools decline with a reason — better than
    inventing a team and writing an item nobody will ever see.
    """
    if not agent_id:
        return (None, None)
    try:
        row = await db.get_one(
            "bus_agent_activity", {"agent_id": agent_id, "state": "running"}
        )
        channel_id = (row or {}).get("channel_id") or ""
        if not channel_id:
            return (None, None)
        channel = await db.get_one("bus_channels", {"channel_id": channel_id})
        created_by = str((channel or {}).get("created_by") or "")
        if not created_by.startswith(TEAM_ROOM_OWNER_PREFIX):
            return (None, None)
        return (created_by[len(TEAM_ROOM_OWNER_PREFIX):], channel_id)
    except Exception as e:  # noqa: BLE001 — resolution failure = no board
        logger.debug(f"[work-board] could not resolve room for {agent_id}: {e}")
        return (None, None)


async def _item_in_my_room(db, agent_id: str, item_id: str):
    """The item, if it belongs to the team this turn is running in — else None.

    `item_id` is globally unique, so a tool that only takes an id will happily
    write across team boundaries. That is reachable without an attacker: an
    agent can belong to several teams, and the work-board section of the prompt
    prints `id=` for every item, so last turn's board from team A is sitting in
    the context while this turn runs in team B.

    Callers must report a plain "not found" on None — "exists but is not yours"
    would leak the other team's ids back into the same context.
    """
    from xyz_agent_context.repository.team_work_repository import (
        TeamWorkItemRepository,
    )

    team_id, _ = await _resolve_team_room(db, agent_id)
    if not team_id:
        return None
    item = await TeamWorkItemRepository(db).get(item_id)
    return item if item and item.team_id == team_id else None


_NO_ROOM = {
    "success": False,
    "error": (
        "The work board only exists inside a team room. This turn is not "
        "running in one, so there is nothing to add the item to."
    ),
}


def _not_found(item_id: str) -> dict:
    return {"success": False, "error": f"Work item not found: {item_id}"}


def register_work_board_mcp_tools(mcp: Any, get_repo_fn: Callable = None) -> None:
    """Register the work-board tools on the module's MCP server.

    Args:
        mcp: The FastMCP server instance.
        get_repo_fn: Optional override for the repository factory (tests).
    """

    async def _repo():
        from xyz_agent_context.repository.team_work_repository import (
            TeamWorkItemRepository,
        )

        if get_repo_fn is not None:
            return await get_repo_fn()
        return TeamWorkItemRepository(await _get_db())

    @mcp.tool()
    async def work_add_item(
        agent_id: str,
        title: str,
        assignee_id: str = "",
    ) -> dict:
        """
        Put a task on the team's work board.

        Use this the moment you hand work out or take work on, so the task
        outlives this turn. A task that only exists in your reply is a task
        nobody can notice has stalled — including you, next time you wake up.

        Args:
            agent_id: Your own agent id.
            title: What is to be done, in one line.
            assignee_id: Agent taking it on. Leave empty for unclaimed work
                anyone can pick up.

        Returns:
            dict with success, item_id.
        """
        db = await _get_db()
        team_id, channel_id = await _resolve_team_room(db, agent_id)
        if not team_id:
            return _NO_ROOM
        repo = await _repo()
        item = await repo.create_item(
            team_id=team_id,
            channel_id=channel_id,
            title=title,
            created_by=agent_id,
            assignee_id=assignee_id or None,
            root_run_id=caller_root_run_id(),
        )
        return {"success": True, "item_id": item.item_id, "status": item.status}

    @mcp.tool()
    async def work_list_items(agent_id: str) -> dict:
        """
        Read the team's unfinished work.

        Shows what is open, in progress, or stalled. Finished, paused and
        cancelled items are not listed — this is the board of what still needs
        doing, not a history.

        Args:
            agent_id: Your own agent id.

        Returns:
            dict with success, items (item_id / title / assignee_id / status).
        """
        db = await _get_db()
        team_id, _ = await _resolve_team_room(db, agent_id)
        if not team_id:
            return _NO_ROOM
        repo = await _repo()
        items = await repo.list_active(team_id)
        return {
            "success": True,
            "items": [
                {
                    "item_id": i.item_id,
                    "title": i.title,
                    "assignee_id": i.assignee_id or "",
                    "status": i.status,
                }
                for i in items
            ],
        }

    @mcp.tool()
    async def work_claim_item(agent_id: str, item_id: str) -> dict:
        """
        Take an unclaimed task and mark it started.

        Args:
            agent_id: Your own agent id.
            item_id: The work item you are taking on.

        Returns:
            dict with success.
        """
        db = await _get_db()
        if not await _item_in_my_room(db, agent_id, item_id):
            return _not_found(item_id)
        repo = await _repo()
        if not await repo.claim(item_id, agent_id):
            return _not_found(item_id)
        return {"success": True, "item_id": item_id, "status": WorkItemStatus.IN_PROGRESS}

    @mcp.tool()
    async def work_complete_item(agent_id: str, item_id: str) -> dict:
        """
        Mark a task finished.

        Do this when you have actually delivered — the board is what tells the
        team lead whether to chase you.

        Args:
            agent_id: Your own agent id.
            item_id: The work item you finished.

        Returns:
            dict with success.
        """
        db = await _get_db()
        if not await _item_in_my_room(db, agent_id, item_id):
            return _not_found(item_id)
        repo = await _repo()
        if not await repo.set_status(item_id, WorkItemStatus.DONE):
            return _not_found(item_id)
        return {"success": True, "item_id": item_id, "status": WorkItemStatus.DONE}

    @mcp.tool()
    async def work_update_status(agent_id: str, item_id: str, status: str) -> dict:
        """
        Move a task between open and in-progress (or back).

        Only "open", "in_progress" and "done" can be set here. "stalled" is
        decided by the platform from real activity data, "paused" is what a
        stop leaves behind, and "cancelled" is the user's call — none of the
        three are yours to write.

        Args:
            agent_id: Your own agent id.
            item_id: The work item to move.
            status: One of "open", "in_progress", "done".

        Returns:
            dict with success.
        """
        if status not in WorkItemStatus.MODEL_SETTABLE:
            allowed = ", ".join(WorkItemStatus.MODEL_SETTABLE)
            return {
                "success": False,
                "error": (
                    f"Status {status!r} cannot be set from here (allowed: "
                    f"{allowed}). 'stalled' is derived from real activity, "
                    f"'paused' comes from the owner stopping the run, and "
                    f"'cancelled' is the user's decision."
                ),
            }
        db = await _get_db()
        if not await _item_in_my_room(db, agent_id, item_id):
            return _not_found(item_id)
        repo = await _repo()
        if not await repo.set_status(item_id, status):
            return _not_found(item_id)
        return {"success": True, "item_id": item_id, "status": status}
