"""
@file_name: channel_ingress_breaker_repository.py
@author:
@date: 2026-08-24
@description: Data access for the message-ingress circuit breaker.

CRUD over ``channel_ingress_breaker`` (one row per session key
``agent_id|channel|chat_id|sender_id``). ``IngressGuard`` owns every escalation
decision; this layer only reads and writes rows — the same split as
``AgentCircuitBreakerRepository`` and its breaker service.

Read volume is deliberately tiny: the guard reads a key ONCE (lazily, on
the first message it sees for that key after process start) and writes only
on tier transitions. The hot path — every inbound message — never touches
this repository.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.schema import ChannelIngressBreaker
from xyz_agent_context.utils.db.dialect_time import event_time_str
from xyz_agent_context.utils.timezone import utc_now

from .base import BaseRepository


class ChannelIngressBreakerRepository(BaseRepository[ChannelIngressBreaker]):
    """Repository for per-conversation ingress breaker state."""

    table_name = "channel_ingress_breaker"
    id_field = "session_key"

    async def get(self, session_key: str) -> Optional[ChannelIngressBreaker]:
        """Return the breaker row for a session key, or None if it has none.

        Never raises: a DB hiccup here must degrade to "no durable state
        known" (the guard then works off memory alone) rather than take the
        whole ingress path down. Fail-open is the correct side for a guard
        that is not an authorization gate.
        """
        try:
            row = await self._db.get_one(self.table_name, {"session_key": session_key})
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ChannelIngressBreakerRepository.get({session_key}): "
                f"{type(e).__name__}: {e} — treating as no durable state"
            )
            return None
        return self._row_to_entity(row) if row else None

    async def upsert_state(self, session_key: str, updates: Dict[str, Any]) -> None:
        """Insert-or-update the session's breaker row with ``updates``.

        Keyed on session_key; always refreshes ``updated_at``. A partial
        write — only the keys in ``updates`` (plus updated_at) are touched
        on an existing row.

        Never raises, for the same reason as ``get``: losing the durable
        copy of a tier transition costs us restart-survival for that one
        session, and the in-memory state still enforces the cooldown for
        the life of the process. Taking the ingress path down instead would
        be strictly worse.
        """
        data = dict(updates)
        data["updated_at"] = utc_now()
        try:
            existing = await self._db.get_one(
                self.table_name, {"session_key": session_key}
            )
            if existing:
                await self._db.update(
                    self.table_name, {"session_key": session_key}, data
                )
            else:
                data["session_key"] = session_key
                await self._db.insert(self.table_name, data)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ChannelIngressBreakerRepository.upsert_state({session_key}): "
                f"{type(e).__name__}: {e} — in-memory state still enforced"
            )

    async def find_open(
        self,
        channel: Optional[str] = None,
        *,
        cooling_only: bool = False,
        now: Optional[datetime] = None,
    ) -> List[ChannelIngressBreaker]:
        """Sessions carrying escalation memory (``tier`` > 0).

        ``cooling_only`` narrows that to the ones whose cooldown has not
        elapsed — i.e. conversations that are suppressed RIGHT NOW.

        The distinction matters because this table only ever grows on the
        ``tier > 0`` side: ``cleanup_older_than_days`` sweeps ``tier = 0``
        rows exclusively, and a session that trips once and never speaks
        again never gets the ``admit()`` calls ``_maybe_recover`` needs to
        walk it back down. So "every row with tier > 0" is closer to a
        lifetime trip log than to a current-state query, and loading it
        into memory on every start would make both the process footprint
        and ``open_session_count()`` climb monotonically with uptime and
        deploy count.
        """
        filters: Dict[str, Any] = {}
        if channel:
            filters["channel"] = channel
        try:
            rows = await self._db.get(self.table_name, filters=filters or None)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ChannelIngressBreakerRepository.find_open({channel}): "
                f"{type(e).__name__}: {e}"
            )
            return []
        entities = [self._row_to_entity(r) for r in rows if r]
        open_rows = [e for e in entities if (e.tier or 0) > 0]
        if not cooling_only:
            return open_rows

        moment = now or utc_now()
        still_cooling = []
        for e in open_rows:
            until = e.cooldown_until
            if until is None:
                continue
            if until.tzinfo is None:
                until = until.replace(tzinfo=moment.tzinfo)
            if moment < until:
                still_cooling.append(e)
        return still_cooling

    async def cleanup_older_than_days(self, days: int) -> int:
        """Delete CLOSED rows (``tier`` = 0) untouched for ``days``.

        Only closed rows are swept. A row with escalation memory is exactly
        the thing we promised to remember — deleting it because it has been
        quiet would hand a re-offender a fresh budget, which is the failure
        mode this table exists to prevent.

        The age comparison happens in PYTHON, not in the WHERE clause. The
        two dialects do not agree on how ``updated_at`` is spelled: sqlite
        round-trips it as ``2026-08-24T10:29:33.197094+00:00`` (isoformat
        default, 'T' separator) while a space-form cutoff string sorts
        differently at that one character.

        Precisely: string comparison is left-to-right, so the 'T'-vs-space
        difference at index 10 only decides the outcome when the date part
        is IDENTICAL. Cross-day comparisons are unaffected; rows on the
        cutoff's own calendar day compare as newer than they are and
        survive a sweep they should not. Narrow — but it is exactly the
        case a same-day retention test hits, which is how the first
        version of this method reported deleting 0 rows.

        ``event_time_str`` is the DB layer's existing answer to this
        asymmetry, and normalising both sides removes the edge entirely.

        Returns:
            Number of rows deleted (best-effort; 0 on driver error).
        """
        cutoff = event_time_str(utc_now() - timedelta(days=days))
        try:
            rows = await self._db.get(self.table_name, filters={"tier": 0})
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ChannelIngressBreakerRepository.cleanup_older_than_days({days}): "
                f"scan failed {type(e).__name__}: {e}"
            )
            return 0
        stale = [
            r["session_key"]
            for r in rows
            if r and event_time_str(r.get("updated_at")) < cutoff
        ]
        deleted = 0
        for key in stale:
            try:
                await self._db.delete(self.table_name, {"session_key": key})
                deleted += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"ChannelIngressBreakerRepository.cleanup_older_than_days: "
                    f"delete({key}) failed {type(e).__name__}: {e}"
                )
        return deleted

    def _row_to_entity(self, row: Dict[str, Any]) -> ChannelIngressBreaker:
        # Pydantic ignores the extra ``id`` column and coerces ISO strings
        # into the model's datetime fields.
        return ChannelIngressBreaker(**row)

    def _entity_to_row(self, entity: ChannelIngressBreaker) -> Dict[str, Any]:
        row = entity.model_dump()
        row.pop("created_at", None)  # DB default handles first insert
        return row
