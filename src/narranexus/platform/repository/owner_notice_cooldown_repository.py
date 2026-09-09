"""
@file_name: owner_notice_cooldown_repository.py
@author:
@date: 2026-09-09
@description: The owner of `owner_notice_cooldowns` — the persisted "last time
we told this owner about this thing" behind the owner-facing SYSTEM_NOTICE
writers (message bus permanent-failure / no-reply notices).

The window itself is the WRITER's constant (e.g. the bus trigger's
``FAILURE_NOTIFY_COOLDOWN_SECONDS``); this layer only remembers when the last
notice landed, keyed per (agent, target, category), so that:

* a process restart does not forget every open window (the in-process dict it
  replaces re-notified on the first poll after every deploy);
* several trigger processes agree on one answer instead of each writing its
  own notice;
* a window opened by channel A never silences an unrelated failure on channel
  B — the old key collapsed every target of a category into one slot.

Plain class (not a ``BaseRepository`` subclass): composite key, two verbs, no
entity type worth naming. Dialect-safe by construction — ``get_one`` /
``insert`` / ``update`` only, no hand-written SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from narranexus.platform.utils.timezone import coerce_utc, utc_now


class OwnerNoticeCooldownRepository:
    TABLE = "owner_notice_cooldowns"

    def __init__(self, db):
        # Untyped on purpose (the family convention): the async DB client is
        # injected; naming its type here would only add a load-order coupling.
        self._db = db

    @staticmethod
    def _key(agent_id: str, target: str, category: str) -> dict:
        return {"agent_id": agent_id, "target": target, "category": category}

    async def last_notified_at(
        self, agent_id: str, target: str, category: str
    ) -> Optional[datetime]:
        """When the last notice for this key landed, as aware UTC; None if never."""
        row = await self._db.get_one(self.TABLE, self._key(agent_id, target, category))
        if not row:
            return None
        return coerce_utc(row.get("last_notified_at"))

    async def is_cooling(
        self, agent_id: str, target: str, category: str, window_seconds: float
    ) -> bool:
        """True while the last notice for this key is younger than the window."""
        last = await self.last_notified_at(agent_id, target, category)
        if last is None:
            return False
        return utc_now() - last < timedelta(seconds=window_seconds)

    async def arm(
        self,
        agent_id: str,
        target: str,
        category: str,
        at: Optional[datetime] = None,
    ) -> None:
        """Record that a notice for this key was just written.

        ``at`` exists so a test can plant an already-expired window; production
        callers leave it None. Insert-or-update on the composite key.
        """
        stamp = at or utc_now()
        key = self._key(agent_id, target, category)
        updated = await self._db.update(self.TABLE, key, {"last_notified_at": stamp})
        if not updated:
            await self._db.insert(self.TABLE, {**key, "last_notified_at": stamp})
