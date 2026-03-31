"""
@file_name: _telegram_dedup.py
@author: NarraNexus
@date: 2026-03-29
@description: Persistent update deduplication for TelegramTrigger

Uses a DB table (telegram_processed_updates) as the source of truth,
with an in-memory cache tier for fast lookups. Composite PK
(update_id, agent_id) ensures per-agent dedup.

Key difference from MatrixEventDedup: update_id is BIGINT (int), not VARCHAR.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from loguru import logger

from xyz_agent_context.utils import DatabaseClient

_MAX_MEMORY_CACHE_PER_AGENT = 10000


class TelegramUpdateDedup:
    """
    Two-tier update deduplication: in-memory cache + DB.

    Usage:
        dedup = TelegramUpdateDedup(db)
        new_ids = await dedup.filter_processed("agent_123", [100, 101, 102])
        # new_ids = [101, 102]  -> process these
        await dedup.mark_processed("agent_123", [101, 102])
    """

    TABLE = "telegram_processed_updates"

    def __init__(self, db: DatabaseClient) -> None:
        self._db = db
        self._memory_cache: dict[str, set[int]] = {}

    async def filter_processed(
        self, agent_id: str, update_ids: list[int]
    ) -> list[int]:
        """
        Return only the update_ids that have NOT been processed yet.

        Checks the in-memory cache first, then falls back to DB for any
        IDs not found in memory.

        Args:
            agent_id: Agent ID
            update_ids: List of Telegram update IDs to check

        Returns:
            List of unprocessed update IDs
        """
        if not update_ids:
            return []

        try:
            cached = self._memory_cache.get(agent_id, set())

            # Fast path: check memory cache
            unknown = [uid for uid in update_ids if uid not in cached]
            already_processed = [uid for uid in update_ids if uid in cached]

            if not unknown:
                # All were found in memory cache
                return []

            # Slow path: check DB for the unknowns
            placeholders = ",".join(["%s"] * len(unknown))
            rows = await self._db.execute(
                f"SELECT update_id FROM {self.TABLE} "
                f"WHERE agent_id = %s AND update_id IN ({placeholders})",
                tuple([agent_id] + unknown),
            )
            db_processed = {row["update_id"] for row in rows}

            # Backfill memory cache with DB results
            if db_processed:
                if agent_id not in self._memory_cache:
                    self._memory_cache[agent_id] = set()
                self._memory_cache[agent_id].update(db_processed)

            # Return IDs that are in neither cache nor DB
            all_processed = set(already_processed) | db_processed
            return [uid for uid in update_ids if uid not in all_processed]

        except Exception as e:
            logger.warning(f"Dedup filter_processed failed: {e}")
            return update_ids  # On error, assume all are new (safe side)

    async def mark_processed(
        self, agent_id: str, update_ids: list[int]
    ) -> None:
        """
        Mark update IDs as processed in both memory cache and DB.

        Uses INSERT IGNORE for idempotency.

        Args:
            agent_id: Agent ID
            update_ids: List of Telegram update IDs to mark
        """
        if not update_ids:
            return

        # Update in-memory cache
        if agent_id not in self._memory_cache:
            self._memory_cache[agent_id] = set()
        self._memory_cache[agent_id].update(update_ids)

        # Bound memory cache size
        if len(self._memory_cache[agent_id]) > _MAX_MEMORY_CACHE_PER_AGENT:
            excess = len(self._memory_cache[agent_id]) - _MAX_MEMORY_CACHE_PER_AGENT
            # Remove oldest entries (smallest update_ids, since IDs are monotonic)
            sorted_ids = sorted(self._memory_cache[agent_id])
            self._memory_cache[agent_id] -= set(sorted_ids[:excess])

        # Persist to DB
        try:
            now = datetime.now(timezone.utc)
            values = [(uid, agent_id, now) for uid in update_ids]
            placeholders = ",".join(["(%s, %s, %s)"] * len(values))
            flat_params = [p for triple in values for p in triple]

            await self._db.execute(
                f"INSERT IGNORE INTO {self.TABLE} "
                f"(update_id, agent_id, processed_at) VALUES {placeholders}",
                tuple(flat_params),
                fetch=False,
            )
        except Exception as e:
            logger.warning(f"Dedup mark_processed DB write failed: {e}")

    async def cleanup_expired(self, retention_days: int = 7) -> int:
        """
        Delete records older than retention_days from DB.

        Args:
            retention_days: Number of days to retain records (default 7)

        Returns:
            Number of deleted rows, or -1 on error
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            deleted = await self._db.execute(
                f"DELETE FROM {self.TABLE} WHERE processed_at < %s",
                (cutoff,),
                fetch=False,
            )
            if deleted > 0:
                logger.info(f"Telegram dedup cleanup: deleted {deleted} expired records")
            return deleted
        except Exception as e:
            logger.warning(f"Telegram dedup cleanup failed: {e}")
            return -1
