"""
@file_name: embedding_migration_service.py
@author: Bin Liang
@date: 2026-03-23
@description: Embedding vector migration/rebuild service

When users switch embedding models, this service scans all entity types
(narrative, event, job, entity) and generates missing embeddings for the
new model. Supports progress tracking and batch processing.

Usage:
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService

    service = EmbeddingMigrationService(db_client)
    status = await service.get_status()       # Check current progress
    await service.rebuild_all()               # Start full rebuild
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from loguru import logger

from xyz_agent_context.agent_framework.api_config import embedding_config
from xyz_agent_context.agent_framework.llm_api.embedding import (
    get_embedding,
    prepare_job_text_for_embedding,
)
from xyz_agent_context.repository.embedding_store_repository import EmbeddingStoreRepository


# =============================================================================
# Status Tracking
# =============================================================================

@dataclass
class MigrationProgress:
    """Current state of an embedding migration"""
    is_running: bool = False
    current_model: str = ""
    # Per-entity-type counts
    total: dict[str, int] = field(default_factory=dict)
    completed: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    # Overall
    error: Optional[str] = None
    finished: bool = False

    @property
    def total_count(self) -> int:
        return sum(self.total.values())

    @property
    def completed_count(self) -> int:
        return sum(self.completed.values())

    @property
    def progress_pct(self) -> float:
        t = self.total_count
        return (self.completed_count / t * 100) if t > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "is_running": self.is_running,
            "current_model": self.current_model,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "total_count": self.total_count,
            "completed_count": self.completed_count,
            "progress_pct": round(self.progress_pct, 1),
            "error": self.error,
            "finished": self.finished,
        }


# Global progress singleton
_progress = MigrationProgress()


def get_migration_progress() -> MigrationProgress:
    return _progress


# =============================================================================
# Source Text Builders (match original embedding generation logic exactly)
# =============================================================================

def _narrative_source_text(row: dict) -> str:
    """Build embedding source text for a narrative (matches updater._regenerate_topic_hint)"""
    # topic_hint is the pre-built source; if available, use it directly
    hint = row.get("topic_hint", "")
    if hint:
        return hint
    name = row.get("name", "")
    summary = row.get("current_summary", "")
    if name and summary:
        return f"{name}: {summary}"
    return summary or name or f"Conversation {row.get('narrative_id', '')}"


def _event_source_text(row: dict) -> str:
    """Build embedding source text for an event (matches processor._generate_embedding)"""
    # The events table stores the pre-built embedding_text
    text = row.get("embedding_text", "")
    if text:
        return text
    # Fallback: reconstruct from input/output
    inp = row.get("input_content", "") or ""
    out = row.get("final_output", "") or ""
    max_len = 2000
    text = inp[:max_len // 2]
    remaining = max_len - len(text)
    if remaining > 50 and out:
        text += " " + out[:remaining]
    return text.strip()


def _job_source_text(row: dict) -> str:
    """Build embedding source text for a job (matches prepare_job_text_for_embedding)"""
    title = row.get("title", "") or ""
    description = row.get("description", "") or ""
    payload = row.get("payload", "") or ""
    return prepare_job_text_for_embedding(title, description, payload)


def _entity_source_text(row: dict) -> str:
    """Build embedding source text for a social entity (matches _entity_updater)"""
    parts = []
    name = row.get("entity_name", "")
    desc = row.get("entity_description", "")
    tags = row.get("tags", "")
    if name:
        parts.append(f"Name: {name}")
    if desc:
        parts.append(f"Description: {desc}")
    if tags:
        if isinstance(tags, list):
            tags = ", ".join(tags)
        parts.append(f"Tags: {tags}")
    return "\n".join(parts)


# =============================================================================
# Migration Service
# =============================================================================

class EmbeddingMigrationService:
    """
    Scans all entity types and generates missing embeddings for the current model.
    """

    # Batch size for embedding generation (avoid overwhelming the API)
    BATCH_SIZE = 20

    def __init__(self, db_client):
        self.db = db_client
        self.emb_repo = EmbeddingStoreRepository(db_client)

    async def get_status(self) -> dict:
        """
        Get the current migration status.

        Checks how many entities have embeddings for the active model
        vs. how many exist in total. In legacy mode (no llm_config.json),
        always returns all_done=True since the old columns are used.
        """
        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import use_embedding_store

        # Legacy mode: embeddings_store not in use, everything is fine
        if not use_embedding_store():
            return {
                "model": embedding_config.model,
                "dimensions": embedding_config.dimensions,
                "stats": {},
                "all_done": True,
                "migration": _progress.to_dict(),
                "legacy_mode": True,
            }

        model = embedding_config.model
        stats = {}

        for entity_type, count_sql in [
            ("narrative", "SELECT COUNT(*) as cnt FROM narratives"),
            ("event", "SELECT COUNT(*) as cnt FROM events WHERE event_embedding IS NOT NULL OR embedding_text IS NOT NULL OR embedding_text != ''"),
            ("job", "SELECT COUNT(*) as cnt FROM instance_jobs"),
            ("entity", "SELECT COUNT(*) as cnt FROM instance_social_entities"),
        ]:
            total_rows = await self.db.execute(count_sql, fetch=True)
            total = total_rows[0]["cnt"] if total_rows else 0
            existing = await self.emb_repo.count_by_model(entity_type, model)
            stats[entity_type] = {
                "total": total,
                "migrated": existing,
                "missing": max(0, total - existing),
            }

        all_done = all(s["missing"] == 0 for s in stats.values())
        return {
            "model": model,
            "dimensions": embedding_config.dimensions,
            "stats": stats,
            "all_done": all_done,
            "migration": _progress.to_dict(),
        }

    async def rebuild_all(self) -> None:
        """
        Rebuild embeddings for all entity types under the current model.

        This runs as a long task. Progress is tracked via get_migration_progress().
        """
        global _progress

        if _progress.is_running:
            logger.warning("Embedding migration already running, skipping")
            return

        model = embedding_config.model
        dims = embedding_config.dimensions

        _progress = MigrationProgress(is_running=True, current_model=model)
        logger.info(f"Starting embedding migration for model={model}")

        try:
            await self._rebuild_narratives(model, dims)
            await self._rebuild_events(model, dims)
            await self._rebuild_jobs(model, dims)
            await self._rebuild_entities(model, dims)

            _progress.finished = True
            logger.info(
                f"Embedding migration completed: "
                f"{_progress.completed_count}/{_progress.total_count} succeeded"
            )
        except Exception as e:
            _progress.error = str(e)
            logger.error(f"Embedding migration failed: {e}")
        finally:
            _progress.is_running = False

    # ---- Per-entity-type rebuild ----

    async def _rebuild_narratives(self, model: str, dims: Optional[int]) -> None:
        entity_type = "narrative"
        rows = await self.db.execute(
            "SELECT narrative_id, name, current_summary, topic_hint FROM narratives",
            fetch=True,
        )
        await self._process_rows(entity_type, model, dims, rows, "narrative_id", _narrative_source_text)

    async def _rebuild_events(self, model: str, dims: Optional[int]) -> None:
        entity_type = "event"
        rows = await self.db.execute(
            "SELECT event_id, embedding_text, "
            "JSON_UNQUOTE(JSON_EXTRACT(env_context, '$.input')) as input_content, "
            "final_output FROM events "
            "WHERE embedding_text IS NOT NULL AND embedding_text != ''",
            fetch=True,
        )
        await self._process_rows(entity_type, model, dims, rows, "event_id", _event_source_text)

    async def _rebuild_jobs(self, model: str, dims: Optional[int]) -> None:
        entity_type = "job"
        rows = await self.db.execute(
            "SELECT job_id, title, description, payload FROM instance_jobs",
            fetch=True,
        )
        await self._process_rows(entity_type, model, dims, rows, "job_id", _job_source_text)

    async def _rebuild_entities(self, model: str, dims: Optional[int]) -> None:
        entity_type = "entity"
        rows = await self.db.execute(
            "SELECT entity_id, entity_name, entity_description, tags FROM instance_social_entities",
            fetch=True,
        )
        await self._process_rows(entity_type, model, dims, rows, "entity_id", _entity_source_text)

    # ---- Core batch processor ----

    async def _process_rows(
        self,
        entity_type: str,
        model: str,
        dims: Optional[int],
        rows: list[dict],
        id_field: str,
        source_text_fn: Callable[[dict], str],
    ) -> None:
        """Process rows in batches, skipping those that already have embeddings."""
        if not rows:
            _progress.total[entity_type] = 0
            _progress.completed[entity_type] = 0
            _progress.failed[entity_type] = 0
            return

        # Filter out rows that already have embeddings for this model
        all_ids = [row[id_field] for row in rows]
        existing = await self.emb_repo.get_vectors_by_ids(entity_type, all_ids, model)
        rows_to_process = [r for r in rows if r[id_field] not in existing]

        _progress.total[entity_type] = len(rows_to_process)
        _progress.completed[entity_type] = 0
        _progress.failed[entity_type] = 0

        logger.info(
            f"[{entity_type}] {len(rows_to_process)} need embedding "
            f"({len(existing)} already done, {len(rows)} total)"
        )

        # Process in batches
        for i in range(0, len(rows_to_process), self.BATCH_SIZE):
            batch = rows_to_process[i:i + self.BATCH_SIZE]
            records = []

            for row in batch:
                entity_id = row[id_field]
                source_text = source_text_fn(row)
                if not source_text.strip():
                    _progress.completed[entity_type] += 1
                    continue
                try:
                    vector = await get_embedding(source_text)
                    actual_dims = len(vector)
                    records.append({
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "model": model,
                        "dimensions": actual_dims,
                        "vector": vector,
                        "source_text": source_text[:2000],  # Truncate for storage
                    })
                except Exception as e:
                    logger.warning(f"[{entity_type}] Failed to embed {entity_id}: {e}")
                    _progress.failed[entity_type] += 1

            # Batch upsert
            if records:
                await self.emb_repo.upsert_batch(records)
                _progress.completed[entity_type] += len(records)

            # Small delay to avoid rate limiting
            if i + self.BATCH_SIZE < len(rows_to_process):
                await asyncio.sleep(0.1)

        logger.info(
            f"[{entity_type}] Done: {_progress.completed[entity_type]} completed, "
            f"{_progress.failed[entity_type]} failed"
        )
