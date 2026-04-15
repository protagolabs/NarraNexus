"""
Chat Message Embedding Repository

CRUD operations for the chat_message_embeddings table.
Stores per-message embeddings for ChatModule conversation history,
enabling embedding-based retrieval of older relevant messages (Part B).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from loguru import logger


@dataclass
class ChatMessageEmbedding:
    instance_id: str
    message_index: int
    role: str = "pair"
    content: str = ""
    embedding: Optional[List[float]] = None
    source_text: Optional[str] = None
    event_id: Optional[str] = None
    created_at: Optional[datetime] = None


class ChatMessageEmbeddingRepository:
    TABLE = "chat_message_embeddings"

    def __init__(self, db):
        self.db = db

    async def upsert(
        self,
        instance_id: str,
        message_index: int,
        content: str,
        embedding: List[float],
        source_text: Optional[str] = None,
        event_id: Optional[str] = None,
        role: str = "pair",
    ) -> None:
        """Insert or update a message embedding."""
        now = datetime.now(timezone.utc)

        existing = await self.db.get_one(
            self.TABLE,
            {"instance_id": instance_id, "message_index": message_index}
        )

        data = {
            "instance_id": instance_id,
            "message_index": message_index,
            "role": role,
            "content": content,
            "embedding": json.dumps(embedding),
            "source_text": source_text[:512] if source_text else None,
            "event_id": event_id,
            "created_at": now,
        }

        if existing:
            await self.db.update(
                self.TABLE,
                {"instance_id": instance_id, "message_index": message_index},
                data
            )
        else:
            await self.db.insert(self.TABLE, data)

    async def get_by_instance(
        self, instance_id: str
    ) -> List[ChatMessageEmbedding]:
        """Get all embeddings for a ChatModule instance."""
        query = f"""
            SELECT * FROM {self.TABLE}
            WHERE instance_id = %s
            ORDER BY message_index ASC
        """
        rows = await self.db.execute(query, (instance_id,), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def get_count(self, instance_id: str) -> int:
        """Get the number of embeddings for an instance."""
        query = f"SELECT COUNT(*) AS cnt FROM {self.TABLE} WHERE instance_id = %s"
        rows = await self.db.execute(query, (instance_id,), fetch=True)
        return rows[0]["cnt"] if rows else 0

    @staticmethod
    def _row_to_entity(row: dict) -> ChatMessageEmbedding:
        embedding = row.get("embedding")
        if isinstance(embedding, str):
            embedding = json.loads(embedding)

        return ChatMessageEmbedding(
            instance_id=row.get("instance_id", ""),
            message_index=row.get("message_index", 0),
            role=row.get("role", "pair"),
            content=row.get("content", ""),
            embedding=embedding,
            source_text=row.get("source_text"),
            event_id=row.get("event_id"),
            created_at=row.get("created_at"),
        )
