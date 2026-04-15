#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create chat_message_embeddings table

Stores per-message embeddings for ChatModule conversation history.
Enables embedding-based retrieval of older relevant messages within a narrative
(Conversation History Part B).

Each row = one conversation turn (user + assistant pair), stored in the same
format used for prompt context building.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_chat_message_embeddings_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_chat_message_embeddings_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root / "src"))


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `chat_message_embeddings` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `instance_id` VARCHAR(128) NOT NULL COMMENT 'ChatModule instance (= user_id + narrative_id pair)',
    `message_index` INT NOT NULL COMMENT 'Position in the messages array (0-based)',
    `role` VARCHAR(16) NOT NULL DEFAULT 'pair' COMMENT 'Message role: pair (user+assistant), user, assistant',
    `content` TEXT NOT NULL COMMENT 'Message content formatted for context (e.g. User: ...\\nAssistant: ...)',
    `embedding` JSON NULL COMMENT 'Embedding vector (1536 dims)',
    `source_text` VARCHAR(512) NULL COMMENT 'Text used for embedding generation (may be truncated)',
    `event_id` VARCHAR(64) NULL COMMENT 'Associated event ID (for traceability)',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_instance_msg` (`instance_id`, `message_index`),
    INDEX `idx_instance` (`instance_id`),
    INDEX `idx_event` (`event_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Per-message embeddings for ChatModule conversation history retrieval (Part B)';
"""

DROP_TABLE_SQL = "DROP TABLE IF EXISTS `chat_message_embeddings`;"


async def create_chat_message_embeddings_table(force: bool = False) -> None:
    """Create the chat_message_embeddings table."""
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        check_table_exists,
    )
    from xyz_agent_context.utils import get_db_client

    table_name = "chat_message_embeddings"
    print(f"\n{'='*60}")
    print(f"Creating table: {table_name}")
    print(f"{'='*60}")

    exists = await check_table_exists(table_name)

    if exists and not force:
        print(f"\nTable `{table_name}` already exists, no need to create.")
        return

    db = await get_db_client()

    if exists and force:
        print(f"Dropping existing table `{table_name}`...")
        await db.execute(DROP_TABLE_SQL)

    print(f"Creating table `{table_name}`...")
    await db.execute(CREATE_TABLE_SQL)
    print(f"✅ Table `{table_name}` created successfully.")


def main():
    parser = argparse.ArgumentParser(description="Create chat_message_embeddings table")
    parser.add_argument("--force", action="store_true", help="Drop and recreate if exists")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_chat_message_embeddings_table(force=args.force))


if __name__ == "__main__":
    main()
