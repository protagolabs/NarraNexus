"""
@file_name: im_short_term_repository.py
@author: NetMind.AI
@date: 2026-06-24
@description: Lightweight cross-turn memory store for distrust IM-channel visitors.

A distrust (static visitor) turn skips the owner's narrative/memory after-execution
hooks, so this table is its ONLY source of cross-turn continuity. Keyed by
(agent_id, im_room_id): a 1:1 DM room is a distinct id → naturally per-sender
(isolated); a group room is one shared id → shared (group messages are public
anyway). This key-derived scoping is the boundary — never a prompt instruction.

Deliberately a plain class (not BaseRepository[T]): rows are append-only with three
operations (append / recent / prune), so the BaseRepository CRUD shape adds nothing.
Mirrors the design of ChannelSeenMessageRepository.

NOTE: this is IM-channel-specific and is NOT the agent-to-agent room table — do not
conflate the two `room_id` concepts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from loguru import logger


class IMShortTermRepository:
    """Append-only short-term IM memory, scoped by (agent_id, im_room_id)."""

    TABLE = "instance_im_short_term"

    def __init__(self, db_client):
        self._db = db_client

    async def append(
        self,
        *,
        agent_id: str,
        owner_id: str,
        channel: str,
        im_room_id: str,
        sender: str,
        role: str,
        body: str,
    ) -> None:
        """Record one message (inbound user or outbound agent) for a room.

        Args:
            agent_id: The agent whose channel this is.
            owner_id: The agent owner (so the owner can later read their channels).
            channel: IM channel name (e.g. "narramessenger", "slack").
            im_room_id: IM-side room/chat/group id (the isolation key).
            sender: IM-side sender identifier.
            role: "user" (inbound) or "agent" (outbound reply).
            body: Message text.
        """
        # Explicit microsecond timestamp — the table DEFAULT (datetime('now')) is
        # only second-resolution on sqlite, which would make same-second ordering
        # ambiguous. recent() orders by the monotonic id anyway, but a precise
        # created_at keeps retention/audit accurate.
        now = datetime.now(timezone.utc).isoformat(sep=" ")
        await self._db.insert(
            self.TABLE,
            {
                "agent_id": agent_id,
                "owner_id": owner_id,
                "channel": channel,
                "im_room_id": im_room_id,
                "sender": sender,
                "role": role,
                "body": body,
                "created_at": now,
            },
        )

    async def recent(
        self, agent_id: str, im_room_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return the latest `limit` messages for (agent_id, im_room_id), oldest-first.

        Ordering is by the monotonic auto-increment id (robust against
        second-resolution timestamp ties), then reversed to chronological order so
        the caller can paste them straight into the prompt.
        """
        rows = await self._db.get(
            self.TABLE,
            filters={"agent_id": agent_id, "im_room_id": im_room_id},
            limit=limit,
            order_by="id DESC",
        )
        return list(reversed(rows))

    async def cleanup_older_than_days(self, days: int) -> int:
        """Delete rows older than `days`. Bounded retention; best-effort.

        Returns the number of rows deleted (0 on driver error — never raises into
        the caller's cleanup tick).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(sep=" ")
        try:
            result = await self._db.execute(
                f"DELETE FROM {self.TABLE} WHERE created_at < %s",
                (cutoff,),
                fetch=False,
            )
            return int(result) if isinstance(result, (int, float)) else 0
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"IMShortTermRepository.cleanup_older_than_days({days}): "
                f"{type(e).__name__}: {e}"
            )
            return 0
