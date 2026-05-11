"""
@file_name: artifact_repository.py
@author: Bin Liang
@date: 2026-05-08
@description: Data-access layer for instance_artifacts + instance_artifact_versions tables.

Provides full CRUD for Artifact rows plus version management:
- create(): atomic insert of artifact row + first version row
- iterate(): bump latest_version and append a new version row (atomic)
- set_pinned(): toggle pinned flag; pinning clears session_id
- list_by_session(): non-pinned artifacts for a given session
- list_pinned(): pinned artifacts for an agent
- list_versions(): all versions for an artifact ordered ASC
- delete(): atomic cascade — delete versions first, then artifact row
- total_bytes_for_agent(): quota query — sum of size_bytes across all
  versions belonging to a given agent's artifacts
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseRepository
from xyz_agent_context.schema.artifact_schema import Artifact, ArtifactVersion


def _parse_dt(v: Any) -> datetime:
    """Parse a datetime value from either a datetime object or an ISO string."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _parse_bool(v: Any) -> bool:
    """Coerce DB integer (0/1) or Python bool to bool."""
    return bool(v)


class ArtifactRepository(BaseRepository[Artifact]):
    """
    Repository for the instance_artifacts table.

    Inherits generic helpers (get_by_id, get_by_ids, find, find_one, save,
    insert, update, delete, upsert) from BaseRepository.

    Extended methods provide version management and quota aggregation.
    """

    table_name = "instance_artifacts"
    id_field = "artifact_id"

    # ── write operations ───────────────────────────────────────────────────────

    async def create(
        self,
        entity: Artifact,
        *,
        file_path: str,
        size_bytes: int,
    ) -> None:
        """
        Insert one artifact row and its first version row.

        Note on atomicity: project-wide convention is single-statement writes
        without explicit transactions (matches the SQLiteProxy backend, which
        does not support nested BEGIN). The two inserts here run sequentially.
        Crash window between the two leaves an artifact row with no version row
        — `list_*` queries simply won't surface it; cleanup is done by deleting
        the orphan via the route DELETE.

        Args:
            entity: The Artifact to persist.
            file_path: Relative path of the content file for version 1.
            size_bytes: Content size in bytes for version 1.
        """
        await self._db.insert(self.table_name, self._entity_to_row(entity))
        await self._db.insert(
            "instance_artifact_versions",
            {
                "artifact_id": entity.artifact_id,
                "version": 1,
                "file_path": file_path,
                "size_bytes": size_bytes,
            },
        )

    async def iterate(
        self,
        artifact_id: str,
        *,
        file_path: str,
        size_bytes: int,
    ) -> int:
        """
        Bump latest_version by 1, append a new version row, and return the new version.

        Args:
            artifact_id: ID of the artifact to iterate.
            file_path: Relative path of the content file for the new version.
            size_bytes: Content size in bytes for the new version.

        Returns:
            The newly created version number.

        Note on concurrency: project-wide convention is single-statement writes
        without explicit transactions (matches the SQLiteProxy backend). Two
        concurrent iterates targeting the same artifact_id can race — both
        could read latest_version=N and try to insert version=N+1 on
        instance_artifact_versions. The unique index `(artifact_id, version)`
        will reject the second insert with a constraint error, and the file on
        disk uses a random hex token so the file naming itself never races.
        Single-user agent flows do not hit this; documented as I9 follow-up.
        """
        row = await self._db.get_one(self.table_name, {self.id_field: artifact_id})
        if row is None:
            raise ValueError(f"Artifact {artifact_id!r} not found")
        new_version: int = int(row["latest_version"]) + 1
        now = datetime.now(timezone.utc).isoformat()

        await self._db.update(
            self.table_name,
            filters={self.id_field: artifact_id},
            data={"latest_version": new_version, "updated_at": now},
        )
        await self._db.insert(
            "instance_artifact_versions",
            {
                "artifact_id": artifact_id,
                "version": new_version,
                "file_path": file_path,
                "size_bytes": size_bytes,
            },
        )

        return new_version

    async def set_pinned(self, artifact_id: str, *, pinned: bool) -> None:
        """
        Toggle pin state.

        On pin: remember current session_id in original_session_id, then clear
        session_id (cross-session visibility).
        On unpin: restore session_id from original_session_id, clear
        original_session_id, set pinned=0.
        If original_session_id was never set (legacy row pinned before this column
        existed), unpin leaves the artifact orphaned with session_id=NULL — the
        route layer is responsible for surfacing a warning to the user (Important #1
        of the review).

        Args:
            artifact_id: ID of the artifact.
            pinned: Target pinned state.
        """
        existing = await self.get_by_id(artifact_id)
        if existing is None:
            raise LookupError(f"artifact not found: {artifact_id}")

        now = datetime.now(timezone.utc)
        if pinned:
            # raw SQL because db.update() filters None and we need to preserve
            # session_id NULL semantics correctly here.
            # COALESCE(original_session_id, ?) preserves a previously saved value
            # so that double-pinning does not overwrite the real original session_id
            # with the already-NULLed session_id from the first pin.
            sql = """
            UPDATE instance_artifacts
            SET pinned = %s, original_session_id = COALESCE(original_session_id, %s), session_id = NULL, updated_at = %s
            WHERE artifact_id = %s
            """
            await self._db.execute(
                sql,
                params=(1, existing.session_id, now, artifact_id),
                fetch=False,
            )
        else:
            # restore session_id from original_session_id; clear original_session_id; set pinned=0
            sql = """
            UPDATE instance_artifacts
            SET pinned = %s, session_id = %s, original_session_id = NULL, updated_at = %s
            WHERE artifact_id = %s
            """
            await self._db.execute(
                sql,
                params=(0, existing.original_session_id, now, artifact_id),
                fetch=False,
            )

    async def delete(self, artifact_id: str) -> None:  # type: ignore[override]
        """
        Delete an artifact and all its version rows.

        Deletes versions first to avoid FK-style orphan rows, then removes
        the artifact row itself. Sequential deletes (no transaction — matches
        project convention; SQLiteProxy backend does not support nested
        BEGIN). Crash window between the two leaves orphan version rows with
        no parent artifact — `list_versions` returns them but nothing else
        references them; cleanup is best-effort via the route's DELETE retry.

        Args:
            artifact_id: ID of the artifact to delete.
        """
        await self._db.delete(
            "instance_artifact_versions",
            {"artifact_id": artifact_id},
        )
        await self._db.delete(
            self.table_name,
            {self.id_field: artifact_id},
        )

    # ── query operations ───────────────────────────────────────────────────────

    async def list_by_session(
        self, agent_id: str, session_id: str
    ) -> List[Artifact]:
        """
        Return non-pinned artifacts for a given session.

        Uses raw SQL because BaseRepository.find() cannot express
        `pinned = 0 AND session_id = ?` with the simple filters dict API
        without an explicit AND of two conditions that include an inequality.

        Args:
            agent_id: Agent scope.
            session_id: Session scope.

        Returns:
            List of non-pinned Artifact objects belonging to the session.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE agent_id = %s AND session_id = %s AND pinned = 0
        """
        rows = await self._db.execute(sql, params=(agent_id, session_id), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def list_pinned(self, agent_id: str) -> List[Artifact]:
        """
        Return pinned artifacts for a given agent.

        Args:
            agent_id: Agent scope.

        Returns:
            List of pinned Artifact objects.
        """
        return await self.find({"agent_id": agent_id, "pinned": 1})

    async def list_versions(self, artifact_id: str) -> List[ArtifactVersion]:
        """
        Return all versions for an artifact ordered by version ASC.

        Args:
            artifact_id: Parent artifact ID.

        Returns:
            List of ArtifactVersion objects ordered by version ascending.
        """
        sql = """
        SELECT * FROM instance_artifact_versions
        WHERE artifact_id = %s
        ORDER BY version ASC
        """
        rows = await self._db.execute(sql, params=(artifact_id,), fetch=True)
        return [self._row_to_version(row) for row in rows]

    async def total_bytes_for_agent(self, agent_id: str) -> int:
        """
        Return the total size_bytes across all versions of all artifacts owned by the agent.

        Joins instance_artifacts to instance_artifact_versions on artifact_id
        so the aggregation is scoped to the given agent's artifacts only.

        Args:
            agent_id: Agent whose quota to sum.

        Returns:
            Sum of size_bytes (0 if agent has no artifacts or versions).
        """
        sql = """
        SELECT COALESCE(SUM(v.size_bytes), 0) AS total
        FROM instance_artifact_versions v
        JOIN instance_artifacts a ON a.artifact_id = v.artifact_id
        WHERE a.agent_id = %s
        """
        rows = await self._db.execute(sql, params=(agent_id,), fetch=True)
        if not rows:
            return 0
        return int(rows[0]["total"] or 0)

    async def count_for_user(self, user_id: str) -> int:
        """
        Return the total artifact count for a user across all their agents.

        Args:
            user_id: User whose quota to count.

        Returns:
            Number of artifacts owned by the user (rows in instance_artifacts).
        """
        sql = "SELECT COUNT(*) AS n FROM instance_artifacts WHERE user_id = %s"
        rows = await self._db.execute(sql, params=(user_id,), fetch=True)
        if not rows:
            return 0
        return int(rows[0]["n"] or 0)

    async def total_bytes_for_user(self, user_id: str) -> int:
        """
        Return the total size_bytes across all versions of all the user's artifacts.

        Joins instance_artifacts to instance_artifact_versions and filters by
        user_id so the aggregation is scoped per-user (cross-agent).

        Args:
            user_id: User whose quota to sum.

        Returns:
            Sum of size_bytes (0 if user has no artifacts or versions).
        """
        sql = """
        SELECT COALESCE(SUM(v.size_bytes), 0) AS total
        FROM instance_artifact_versions v
        JOIN instance_artifacts a ON a.artifact_id = v.artifact_id
        WHERE a.user_id = %s
        """
        rows = await self._db.execute(sql, params=(user_id,), fetch=True)
        if not rows:
            return 0
        return int(rows[0]["total"] or 0)

    async def list_by_user(self, user_id: str) -> List[Artifact]:
        """
        Return all artifacts owned by a user, across every agent the user owns.

        Ordered by updated_at DESC so the freshest activity surfaces first
        in the Settings → Artifacts management table.

        Args:
            user_id: User scope.

        Returns:
            List of Artifact objects belonging to the user, newest first.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE user_id = %s
        ORDER BY updated_at DESC
        """
        rows = await self._db.execute(sql, params=(user_id,), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def bulk_delete(self, artifact_ids: List[str]) -> int:
        """
        Delete multiple artifacts (and their versions) in one call.

        Order matters: versions first to mirror the per-row delete contract.
        Uses one DELETE per id rather than a bulk IN(...) so callers that
        wrap this in a transaction (when one is supported) can roll back
        cleanly. Returns the number of artifact rows actually removed.

        On-disk folder cleanup is the route's responsibility — this method
        only touches the DB.

        Args:
            artifact_ids: Artifact IDs to delete. Empty list → no-op.

        Returns:
            Number of parent rows deleted (filtered to those that existed).
        """
        if not artifact_ids:
            return 0
        deleted = 0
        for aid in artifact_ids:
            await self._db.delete("instance_artifact_versions", {"artifact_id": aid})
            n = await self._db.delete(self.table_name, {self.id_field: aid})
            deleted += int(n or 0)
        return deleted

    # ── conversion helpers ─────────────────────────────────────────────────────

    def _row_to_entity(self, row: Dict[str, Any]) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            agent_id=row["agent_id"],
            user_id=row["user_id"],
            session_id=row.get("session_id"),
            original_session_id=row.get("original_session_id"),
            title=row["title"],
            kind=row["kind"],
            description=row.get("description"),
            pinned=_parse_bool(row.get("pinned", 0)),
            latest_version=int(row.get("latest_version", 1)),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _entity_to_row(self, entity: Artifact) -> Dict[str, Any]:
        return {
            "artifact_id": entity.artifact_id,
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "session_id": entity.session_id,
            "original_session_id": entity.original_session_id,
            "title": entity.title,
            "kind": entity.kind,
            "description": entity.description,
            "pinned": 1 if entity.pinned else 0,
            "latest_version": entity.latest_version,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }

    def _row_to_version(self, row: Dict[str, Any]) -> ArtifactVersion:
        return ArtifactVersion(
            id=int(row["id"]),
            artifact_id=row["artifact_id"],
            version=int(row["version"]),
            file_path=row["file_path"],
            size_bytes=int(row["size_bytes"]),
            created_at=_parse_dt(row["created_at"]),
        )
