"""
@file_name: agent_api_key_repository.py
@author: NarraNexus
@date: 2026-06-11
@description: CRUD repository for the agent_api_keys table.

Backs the /api/agents/{agent_id}/api-keys management endpoints and the
external API middleware's `nxk_` token lookup. The DB row carries
SHA256(token), not plaintext — see utils/api_key_token.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.schema.agent_api_key_schema import AgentApiKey


_TABLE = "agent_api_keys"


def _row_to_entity(row: Dict[str, Any]) -> AgentApiKey:
    """Convert a DB row dict into a Pydantic AgentApiKey."""
    raw_scopes = row.get("scopes")
    if isinstance(raw_scopes, str):
        try:
            scopes = json.loads(raw_scopes)
        except json.JSONDecodeError:
            scopes = []
    elif isinstance(raw_scopes, list):
        scopes = raw_scopes
    else:
        scopes = []

    raw_meta = row.get("metadata")
    if isinstance(raw_meta, str):
        try:
            metadata = json.loads(raw_meta) if raw_meta else None
        except json.JSONDecodeError:
            metadata = None
    elif isinstance(raw_meta, dict):
        metadata = raw_meta
    else:
        metadata = None

    return AgentApiKey(
        id=row.get("id"),
        key_id=row["key_id"],
        token_hash=row["token_hash"],
        token_prefix=row["token_prefix"],
        agent_id=row["agent_id"],
        owner_user_id=row["owner_user_id"],
        name=row["name"],
        scopes=scopes,
        expires_at=_parse_dt(row.get("expires_at")),
        revoked_at=_parse_dt(row.get("revoked_at")),
        last_used_at=_parse_dt(row.get("last_used_at")),
        metadata=metadata,
        created_at=_parse_dt(row.get("created_at")),
        updated_at=_parse_dt(row.get("updated_at")),
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    """Coerce a DB datetime field (string/datetime/None) into a
    timezone-aware UTC datetime.

    Storage convention is UTC. Some DB drivers (notably SQLite, which
    returns 'YYYY-MM-DD HH:MM:SS' strings) drop the tz suffix; those rows
    are assumed UTC and tzinfo is attached. Without this, downstream
    `expires_at < utc_now()` checks crash with TypeError ("can't compare
    offset-naive and offset-aware datetimes") whenever a rotated key has
    a grace `expires_at` set.
    """
    from datetime import timezone

    if value is None:
        return None
    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class AgentApiKeyRepository:
    """Persistence layer for `agent_api_keys`."""

    def __init__(self, db: Any):
        self._db = db

    async def insert(
        self,
        *,
        key_id: str,
        token_hash: str,
        token_prefix: str,
        agent_id: str,
        owner_user_id: str,
        name: str,
        scopes: List[str],
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentApiKey:
        """Create a row and return the freshly stored entity."""
        now = datetime.now(timezone.utc)
        payload: Dict[str, Any] = {
            "key_id": key_id,
            "token_hash": token_hash,
            "token_prefix": token_prefix,
            "agent_id": agent_id,
            "owner_user_id": owner_user_id,
            "name": name,
            "scopes": json.dumps(scopes),
            "created_at": now,
            "updated_at": now,
        }
        if expires_at is not None:
            payload["expires_at"] = expires_at
        if metadata is not None:
            payload["metadata"] = json.dumps(metadata)
        await self._db.insert(_TABLE, payload)

        row = await self._db.get_one(_TABLE, {"key_id": key_id})
        if not row:
            raise RuntimeError(f"agent_api_keys insert failed: key_id={key_id!r}")
        return _row_to_entity(row)

    async def get_by_key_id(self, key_id: str) -> Optional[AgentApiKey]:
        """O(1) lookup used by the external API middleware."""
        row = await self._db.get_one(_TABLE, {"key_id": key_id})
        return _row_to_entity(row) if row else None

    async def list_for_agent(
        self,
        agent_id: str,
        *,
        include_revoked: bool = True,
    ) -> List[AgentApiKey]:
        """List every key for an agent. UI uses `include_revoked=True` so
        revoked rows still show with status="revoked" — owners value
        seeing the history.
        """
        filters: Dict[str, Any] = {"agent_id": agent_id}
        rows = await self._db.get(
            _TABLE,
            filters=filters,
            order_by="created_at DESC",
            limit=200,
        )
        keys = [_row_to_entity(r) for r in rows]
        if not include_revoked:
            keys = [k for k in keys if k.revoked_at is None]
        return keys

    async def update(
        self,
        key_id: str,
        updates: Dict[str, Any],
    ) -> int:
        """Apply a partial update. Keys are: name / scopes / expires_at /
        metadata / revoked_at / last_used_at. JSON fields are serialised
        on write.
        """
        if not updates:
            return 0

        prepared: Dict[str, Any] = {}
        for col, value in updates.items():
            if col in ("scopes", "metadata") and value is not None:
                prepared[col] = json.dumps(value)
            else:
                prepared[col] = value
        prepared["updated_at"] = datetime.now(timezone.utc)

        return await self._db.update(_TABLE, {"key_id": key_id}, prepared)

    async def revoke(
        self,
        key_id: str,
        *,
        revoke_at: Optional[datetime] = None,
    ) -> int:
        """Soft delete by writing `revoked_at`. Default now."""
        ts = revoke_at or datetime.now(timezone.utc)
        return await self.update(key_id, {"revoked_at": ts})

    async def touch_last_used(self, key_id: str) -> None:
        """Best-effort `last_used_at` bump from the request middleware.
        Failures are logged at warning level but never raised — chat
        latency must NOT depend on this write succeeding.
        """
        try:
            await self.update(key_id, {"last_used_at": datetime.now(timezone.utc)})
        except Exception as exc:
            logger.warning(
                "agent_api_keys: failed to touch last_used_at for "
                "key_id={!r}: {}",
                key_id,
                exc,
            )
