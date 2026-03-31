#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file_name: create_telegram_processed_updates_table.py
@author: NarraNexus
@date: 2026-03-29
@description: Create telegram_processed_updates table

Persistent dedup layer for TelegramTrigger.
Stores update IDs that have been processed by each agent,
so that trigger restarts don't cause re-processing.

Kept lightweight: simple (update_id, agent_id) composite key
with TTL cleanup (7 days default).

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_telegram_processed_updates_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_telegram_processed_updates_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
    )


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `telegram_processed_updates` (
    `update_id` BIGINT NOT NULL COMMENT 'Telegram update_id',
    `agent_id` VARCHAR(64) NOT NULL COMMENT 'Agent that processed this update',
    `processed_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`update_id`, `agent_id`),
    INDEX `idx_processed_at` (`processed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Persistent dedup for TelegramTrigger -- survives restarts';
"""


async def create_telegram_processed_updates_table(force: bool = False) -> None:
    """Create the telegram_processed_updates table."""
    await create_table(
        table_name="telegram_processed_updates",
        create_sql=CREATE_TABLE_SQL,
        force=force,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Create telegram_processed_updates table"
    )
    parser.add_argument(
        "--force", action="store_true", help="Drop and recreate if exists"
    )
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_telegram_processed_updates_table(force=args.force))


if __name__ == "__main__":
    main()
