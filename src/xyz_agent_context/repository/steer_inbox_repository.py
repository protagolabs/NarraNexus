"""
@file_name: steer_inbox_repository.py
@author: Bin Liang
@date: 2026-08-21
@description: The owner of `steer_inbox` — the durable store for
live-steering injections destined for a running turn.

A producer appends one injection keyed by an OPAQUE ``run_id`` (the
RunRegistry's handle for a live run — this layer never interprets it);
the transport drains a run's unconsumed rows into its ``SteeringInlet``
at the next step boundary and marks them consumed.

Plain class rather than a ``BaseRepository`` subclass: the id is an
auto-increment integer and every access is a scoped range query, so
entity plumbing would be ceremony (same reasoning as
``ArtifactEventRepository``). Reads still return ``SteerInjection`` so
callers get provenance (source/sender) typed, not raw dicts.
"""

from __future__ import annotations

from typing import List

from loguru import logger

from xyz_agent_context.schema.steer_schema import SteerInjection
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.dialect_errors import is_unique_violation
from xyz_agent_context.utils.timezone import utc_now


class SteerInboxRepository:
    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    async def append(self, inj: SteerInjection) -> bool:
        """Persist one injection. Returns True if inserted, False if a row
        with the same ``(run_id, msg_id)`` already exists.

        Idempotent so a re-delivered message injects at most once. The
        unique index is the hard guarantee; the pre-check handles the
        common case cleanly, and a lost insert race (two producers, same
        message) surfaces as a unique-key violation which is reported as
        the duplicate it is — never a false new-row. Only that specific
        violation is swallowed: any other insert failure (a real bug) still
        raises rather than masquerading as a duplicate.
        """
        existing = await self._db.execute(
            "SELECT id FROM steer_inbox WHERE run_id = %s AND msg_id = %s LIMIT 1",
            params=(inj.run_id, inj.msg_id),
            fetch=True,
        )
        if existing:
            return False
        try:
            await self._db.insert(
                "steer_inbox",
                {
                    "run_id": inj.run_id,
                    "msg_id": inj.msg_id,
                    "role": inj.role,
                    "content": inj.content,
                    "sender_id": inj.sender_id,
                    "source": inj.source,
                },
            )
        except Exception as e:  # noqa: BLE001 — narrowed to the unique pair below
            if is_unique_violation(e):
                logger.debug(
                    f"[steer-inbox] append lost the insert race for "
                    f"{inj.run_id}/{inj.msg_id}: {e}"
                )
                return False
            raise
        return True

    async def pull_unconsumed(self, run_id: str) -> List[SteerInjection]:
        """The run's still-pending injections, oldest first (arrival order)."""
        rows = await self._db.execute(
            "SELECT id, run_id, msg_id, role, content, sender_id, source, "
            "created_at, consumed_at FROM steer_inbox "
            "WHERE run_id = %s AND consumed_at IS NULL ORDER BY id",
            params=(run_id,),
            fetch=True,
        )
        return [SteerInjection(**row) for row in (rows or [])]

    async def mark_consumed(self, run_id: str, up_to_id: int) -> int:
        """Stamp ``consumed_at`` on this run's pending rows with id <=
        ``up_to_id``. Returns how many rows it consumed.

        Scoped to ``run_id`` and to ``consumed_at IS NULL`` so it never
        touches another run's backlog and re-consuming is a no-op. The id
        ceiling is what lets a drain consume exactly the window it saw and
        leave anything that arrived after it pending (no silent loss)."""
        result = await self._db.execute(
            "UPDATE steer_inbox SET consumed_at = %s "
            "WHERE run_id = %s AND id <= %s AND consumed_at IS NULL",
            params=(utc_now(), run_id, up_to_id),
            fetch=False,
        )
        return result if isinstance(result, int) else 0
