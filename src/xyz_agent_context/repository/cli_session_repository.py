"""
@file_name: cli_session_repository.py
@author:
@date: 2026-07-24
@description: Data access for resumable coding-agent CLI session handles.

CRUD over ``agent_cli_sessions``, keyed on the unique triple
(agent_id, platform_session_id, framework). The runtime owns all resume
decision logic (step_3 lookup + validation, step_4 persistence); this layer
only reads/writes rows.

``upsert`` is a two-step get→insert/update on purpose (no atomic
INSERT ... ON DUPLICATE KEY): it only ever runs in step_4's fire-and-forget
persistence context, where the race window is harmless — concurrent turns
on the same key mean the later write overwrites the earlier one, which is
exactly the wanted semantics (latest CLI session wins).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from xyz_agent_context.schema.cli_session import AgentCliSession
from xyz_agent_context.utils.timezone import utc_now

from .base import BaseRepository


class CliSessionRepository(BaseRepository[AgentCliSession]):
    """Repository for resumable CLI session handles."""

    table_name = "agent_cli_sessions"
    id_field = "id"

    @staticmethod
    def _key_filters(
        agent_id: str, platform_session_id: str, framework: str
    ) -> Dict[str, Any]:
        return {
            "agent_id": agent_id,
            "platform_session_id": platform_session_id,
            "framework": framework,
        }

    async def get(
        self, agent_id: str, platform_session_id: str, framework: str
    ) -> Optional[AgentCliSession]:
        """Return the handle for the key triple, or None if absent."""
        row = await self._db.get_one(
            self.table_name,
            self._key_filters(agent_id, platform_session_id, framework),
        )
        return self._row_to_entity(row) if row else None

    async def upsert(self, entity: AgentCliSession) -> int:
        """Insert-or-update the handle keyed on the unique triple.

        On an existing row, only the handle payload columns are refreshed
        (cli_session_id / config_fingerprint / working_path / narrative_id /
        last_used_at + updated_at) — the surrogate id and created_at stay.

        Returns:
            Number of affected rows (insert returns the new row id).
        """
        filters = self._key_filters(
            entity.agent_id, entity.platform_session_id, entity.framework
        )
        existing = await self._db.get_one(self.table_name, filters)
        if existing:
            logger.debug(
                f"    → CliSessionRepository.upsert: updating {filters}"
            )
            return await self._db.update(
                self.table_name,
                filters=filters,
                data={
                    "cli_session_id": entity.cli_session_id,
                    "config_fingerprint": entity.config_fingerprint,
                    "working_path": entity.working_path,
                    "narrative_id": entity.narrative_id,
                    "last_used_at": entity.last_used_at,
                    "updated_at": utc_now(),
                },
            )
        logger.debug(f"    → CliSessionRepository.upsert: inserting {filters}")
        return await self._db.insert(self.table_name, self._entity_to_row(entity))

    async def delete_handle(
        self, agent_id: str, platform_session_id: str, framework: str
    ) -> int:
        """Delete the handle for the key triple (e.g. after a failed resume).

        Named ``delete_handle`` (not ``delete``) because the base class's
        ``delete(entity_id)`` is keyed on the surrogate id — an override
        with the composite key would be signature-incompatible.

        Returns:
            Number of deleted rows (0 when the handle did not exist).
        """
        return await self._db.delete(
            self.table_name,
            self._key_filters(agent_id, platform_session_id, framework),
        )

    def _row_to_entity(self, row: Dict[str, Any]) -> AgentCliSession:
        # Pydantic coerces ISO strings into datetimes and tolerates the
        # surrogate id column.
        return AgentCliSession(**row)

    def _entity_to_row(self, entity: AgentCliSession) -> Dict[str, Any]:
        row = entity.model_dump()
        row.pop("id", None)  # Auto-increment surrogate key
        row.pop("created_at", None)  # DB default handles first insert
        row.pop("updated_at", None)  # DB default handles first insert
        return row
