"""
@file_name: agent_circuit_breaker_repository.py
@author:
@date: 2026-07-13
@description: Data access for the real-time-layer Agent circuit-breaker.

CRUD over ``instance_agent_circuit_breaker`` (one row per agent_id). The
breaker service owns all the escalation logic; this layer only reads/writes
rows. ``upsert_state`` is the workhorse — a partial, agent_id-keyed
insert-or-update that stamps ``updated_at``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from narranexus.platform.schema import AgentCircuitBreaker, CbStatus
from narranexus.platform.utils.timezone import utc_now

from .base import BaseRepository


class AgentCircuitBreakerRepository(BaseRepository[AgentCircuitBreaker]):
    """Repository for Agent circuit-breaker state."""

    table_name = "instance_agent_circuit_breaker"
    id_field = "agent_id"

    async def get(self, agent_id: str) -> Optional[AgentCircuitBreaker]:
        """Return the breaker row for an agent, or None if it has none yet."""
        row = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        return self._row_to_entity(row) if row else None

    async def upsert_state(self, agent_id: str, updates: Dict[str, Any]) -> None:
        """Insert-or-update the agent's breaker row with ``updates``.

        Keyed on agent_id; always refreshes ``updated_at``. A partial write —
        only the keys in ``updates`` (plus updated_at) are touched on an
        existing row.
        """
        data = dict(updates)
        data["updated_at"] = utc_now()
        existing = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        if existing:
            await self._db.update(self.table_name, {"agent_id": agent_id}, data)
        else:
            data["agent_id"] = agent_id
            await self._db.insert(self.table_name, data)

    async def find_by_status(self, status: str) -> List[AgentCircuitBreaker]:
        """All breaker rows currently in the given ``cb_status``."""
        rows = await self._db.get(self.table_name, filters={"cb_status": status})
        return [self._row_to_entity(r) for r in rows if r]

    async def try_claim_probe(
        self, agent_id: str, from_status: str, grant_until: Any
    ) -> bool:
        """Atomically transition ``from_status`` -> PROBING, granting the
        caller the single half-open probe turn.

        Equality-filtered ``UPDATE ... WHERE agent_id=? AND cb_status=?`` is
        the compare-and-swap: only the caller whose write actually matches
        the current ``cb_status`` flips it, so concurrent callers racing the
        same expired PAUSED row can never both win. ``grant_until`` re-stamps
        ``cooldown_until`` as the probe's own expiry — if the winning turn
        crashes without recording an outcome (record_success/record_failure
        never runs), a later ``should_skip`` sees a stale PROBING row and can
        re-claim it via the same call (``from_status=PROBING``), so a dead
        probe self-heals instead of jamming the breaker open forever.

        Returns True iff this call won the race (rowcount > 0).
        """
        rowcount = await self._db.update(
            self.table_name,
            {"agent_id": agent_id, "cb_status": from_status},
            {
                "cb_status": CbStatus.PROBING.value,
                "cooldown_until": grant_until,
                "updated_at": utc_now(),
            },
        )
        return rowcount > 0

    async def find_paused(self) -> List[AgentCircuitBreaker]:
        """All agents currently in PAUSED state (any reason)."""
        return await self.find_by_status(CbStatus.PAUSED.value)

    def _row_to_entity(self, row: Dict[str, Any]) -> AgentCircuitBreaker:
        # Pydantic ignores the extra ``id`` column and coerces ISO strings /
        # enum strings into the model's types.
        return AgentCircuitBreaker(**row)

    def _entity_to_row(self, entity: AgentCircuitBreaker) -> Dict[str, Any]:
        row = entity.model_dump()
        row.pop("created_at", None)  # DB default handles first insert
        return row
