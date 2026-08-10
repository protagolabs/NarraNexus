"""
@file_name: team_work_repository.py
@author:
@date: 2026-08-07
@description: Data access for the team work board.

Lives here rather than inside the message-bus module because repositories are
project-wide by convention (``repository/``, never inside a module) — and this
one has two consumers that are not the module: the patrol lane in
``MessageBusTrigger`` and the cancel endpoint's stop→pause link.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import BaseRepository
from xyz_agent_context.schema.team_work_schema import WorkItem, WorkItemStatus


class TeamWorkItemRepository(BaseRepository[WorkItem]):
    table_name = "team_work_items"
    id_field = "item_id"

    @staticmethod
    def gen_item_id() -> str:
        return f"wi_{secrets.token_hex(4)}"

    def _row_to_entity(self, row: Dict[str, Any]) -> WorkItem:
        return WorkItem(**row)

    def _entity_to_row(self, entity: WorkItem) -> Dict[str, Any]:
        return entity.model_dump(exclude={"id"})

    # ===== Reads =====

    async def get(self, item_id: str) -> Optional[WorkItem]:
        """One item, or None. None is a normal answer: item ids reach us from
        model-authored tool calls, so a typo must read as "not found" rather
        than raise something the agent reports to the user as a platform fault.
        """
        if not item_id:
            return None
        row = await self._db.get_one(self.table_name, {"item_id": item_id})
        return self._row_to_entity(row) if row else None

    async def list_active(self, team_id: str) -> List[WorkItem]:
        """Items patrol should still care about, oldest first.

        ``paused`` is excluded by ``WorkItemStatus.ACTIVE`` and that exclusion
        is the entire point of the state: a stopped tree's items must stop
        producing patrol work, or the owner's stop gets undone by the next
        patrol cycle.
        """
        if not team_id:
            return []
        marks = ",".join(["%s"] * len(WorkItemStatus.ACTIVE))
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} "
            f"WHERE team_id = %s AND status IN ({marks}) "
            f"ORDER BY created_at ASC",
            (team_id, *WorkItemStatus.ACTIVE),
        )
        return [self._row_to_entity(r) for r in rows or []]

    async def list_visible(self, team_id: str) -> List[WorkItem]:
        """What the USER's board shows: unfinished work plus parked items.

        Differs from ``list_active`` by one state, and that state is the whole
        point: ``paused`` is what a stop leaves behind, and deciding whether to
        resume it is the user's call — hiding it (as the agent-facing list
        does) would make a stopped task look deleted.

        Lives here rather than as raw SQL in the route so both queries share
        one dialect surface: the project requires new hand-written SQL to be
        validated against a real MySQL, and a second copy in `routes/` would be
        a second thing to keep covered.
        """
        if not team_id:
            return []
        states = (*WorkItemStatus.ACTIVE, WorkItemStatus.PAUSED)
        marks = ",".join(["%s"] * len(states))
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} "
            f"WHERE team_id = %s AND status IN ({marks}) "
            f"ORDER BY created_at ASC",
            (team_id, *states),
        )
        return [self._row_to_entity(r) for r in rows or []]

    async def teams_with_active_work(self) -> List[str]:
        """Teams with at least one unfinished item.

        The patrol lane's candidate filter. One query for the whole fleet —
        the shape ``_agents_with_pending`` established, for the same reason:
        the alternative is asking every team every cycle just to learn it has
        nothing to do.
        """
        marks = ",".join(["%s"] * len(WorkItemStatus.ACTIVE))
        rows = await self._db.execute(
            f"SELECT DISTINCT team_id FROM {self.table_name} "
            f"WHERE status IN ({marks})",
            tuple(WorkItemStatus.ACTIVE),
        )
        return [r["team_id"] for r in rows or []]

    # ===== Writes =====

    async def create_item(
        self,
        *,
        team_id: str,
        channel_id: str,
        title: str,
        created_by: str,
        assignee_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        root_run_id: Optional[str] = None,
    ) -> WorkItem:
        item = WorkItem(
            item_id=self.gen_item_id(),
            team_id=team_id,
            channel_id=channel_id,
            title=title,
            assignee_id=assignee_id or None,
            status=WorkItemStatus.IN_PROGRESS if assignee_id else WorkItemStatus.OPEN,
            created_by=created_by,
            source_message_id=source_message_id or None,
            root_run_id=root_run_id or None,
        )
        await self._db.insert(self.table_name, self._entity_to_row(item))
        return item

    async def set_status(self, item_id: str, status: str) -> bool:
        """Move one item. Returns False for an unknown id (see ``get``)."""
        if not item_id or not await self.get(item_id):
            return False
        await self._db.update(
            self.table_name, {"item_id": item_id}, {"status": status}
        )
        return True

    async def claim(self, item_id: str, assignee_id: str) -> bool:
        """Take ownership of an item and start it."""
        if not item_id or not assignee_id or not await self.get(item_id):
            return False
        await self._db.update(
            self.table_name,
            {"item_id": item_id},
            {"assignee_id": assignee_id, "status": WorkItemStatus.IN_PROGRESS},
        )
        return True

    async def pause_by_root(self, root_run_id: str) -> int:
        """Pause every ACTIVE item of one trigger tree. Returns rows changed.

        Called when the owner stops a run tree. Pausing rather than cancelling
        is the owner's decision (2026-08-07): a stop means "stop running", not
        "abandon the task", so the item stays on the board and resuming is an
        explicit action.

        An empty ``root_run_id`` is a no-op, NOT a match-all: items predating
        the column carry NULL, and treating that as "the same tree" would let
        one stop freeze the entire board.
        """
        if not root_run_id:
            return 0
        items = [
            i for i in await self._by_root(root_run_id)
            if i.status in WorkItemStatus.ACTIVE
        ]
        changed = 0
        for item in items:
            try:
                await self._db.update(
                    self.table_name,
                    {"item_id": item.item_id},
                    {"status": WorkItemStatus.PAUSED},
                )
                changed += 1
            except Exception as e:  # noqa: BLE001
                # One unwritable row must not abort the rest: a partially
                # paused board still beats a board that keeps reviving work.
                logger.warning(
                    f"[work-board] could not pause {item.item_id!r}: {e}"
                )
        return changed

    async def _by_root(self, root_run_id: str) -> List[WorkItem]:
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} WHERE root_run_id = %s",
            (root_run_id,),
        )
        return [self._row_to_entity(r) for r in rows or []]
