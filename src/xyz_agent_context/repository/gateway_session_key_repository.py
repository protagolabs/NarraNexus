"""
@file_name: gateway_session_key_repository.py
@author: Bin Liang
@date: 2026-07-23
@description: Data-access layer for the `instance_gateway_session_keys` table.

Tracks per-run LiteLLM gateway session keys so they can be revoked at run end
and reaped when a crash leaves them orphaned (active row, dead run). See
gateway_key_service.GatewayKeyService for the lifecycle that drives it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseRepository, parse_dt
from xyz_agent_context.schema.gateway_session_key_schema import (
    GatewaySessionKey,
    GatewaySessionKeyStatus,
)


class GatewaySessionKeyRepository(BaseRepository[GatewaySessionKey]):
    table_name = "instance_gateway_session_keys"
    id_field = "run_id"  # logical key; surrogate PK `id` is table-internal

    async def create(
        self,
        run_id: str,
        user_id: str,
        agent_id: Optional[str] = None,
        key_hash: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        entity = GatewaySessionKey(
            run_id=run_id,
            user_id=user_id,
            agent_id=agent_id,
            key_hash=key_hash,
            status=GatewaySessionKeyStatus.ACTIVE,
            created_at=now,
        )
        await self._db.insert(self.table_name, self._entity_to_row(entity))

    async def mark_revoked(self, run_id: str) -> None:
        """Flip a row to revoked. Idempotent: re-revoking a revoked row is a
        harmless no-op (the WHERE still matches, revoked_at just refreshes)."""
        await self._db.update(
            self.table_name,
            {"run_id": run_id},
            {
                "status": GatewaySessionKeyStatus.REVOKED.value,
                "revoked_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def list_unmetered_revoked(
        self, older_than_seconds: int, limit: int = 200
    ) -> List[GatewaySessionKey]:
        """Revoked (finished) runs whose usage has NOT yet been metered, and
        which were revoked long enough ago that LiteLLM's SpendLogs have flushed.
        Drives gateway_spend_reconciler. `metered_at IS NULL` is the idempotency
        guard; the age floor avoids missing the run's last request."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        # get() only does equality filters; use a raw query for the NULL + range.
        sql = f"""
        SELECT * FROM {self.table_name}
        WHERE status = %s AND metered_at IS NULL
          AND revoked_at IS NOT NULL AND revoked_at < %s
        ORDER BY revoked_at ASC
        LIMIT {int(limit)}
        """
        rows = await self._db.execute(
            sql,
            params=(GatewaySessionKeyStatus.REVOKED.value, cutoff),
            fetch=True,
        )
        return [self._row_to_entity(r) for r in rows]

    async def mark_metered(self, run_id: str) -> None:
        """Stamp a run as metered so the reconciler never charges it twice."""
        await self._db.update(
            self.table_name,
            {"run_id": run_id},
            {"metered_at": datetime.now(timezone.utc).isoformat()},
        )

    async def list_active_for_user(self, user_id: str) -> List[GatewaySessionKey]:
        """All ACTIVE keys for a user. Used by the executor-reaper hook: when a
        user's idle executor is culled (zero active loops, proven by the
        admission controller), any ACTIVE key of theirs is a crash orphan and is
        safe to revoke — no timer, no live-run guessing (铁律 #14)."""
        rows = await self._db.get(
            self.table_name,
            {"user_id": user_id, "status": GatewaySessionKeyStatus.ACTIVE.value},
        )
        return [self._row_to_entity(r) for r in rows]

    def _row_to_entity(self, row: Dict[str, Any]) -> GatewaySessionKey:
        return GatewaySessionKey(
            run_id=row["run_id"],
            user_id=row["user_id"],
            agent_id=row.get("agent_id"),
            key_hash=row.get("key_hash"),
            status=GatewaySessionKeyStatus(row["status"]),
            created_at=parse_dt(row["created_at"]),
            revoked_at=parse_dt(row["revoked_at"]) if row.get("revoked_at") else None,
            metered_at=parse_dt(row["metered_at"]) if row.get("metered_at") else None,
        )

    def _entity_to_row(self, entity: GatewaySessionKey) -> Dict[str, Any]:
        return {
            "run_id": entity.run_id,
            "user_id": entity.user_id,
            "agent_id": entity.agent_id,
            "key_hash": entity.key_hash,
            "status": entity.status.value,
            "created_at": entity.created_at.isoformat(),
            "revoked_at": entity.revoked_at.isoformat() if entity.revoked_at else None,
        }
