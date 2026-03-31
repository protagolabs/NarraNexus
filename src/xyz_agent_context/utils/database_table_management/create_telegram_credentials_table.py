#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file_name: create_telegram_credentials_table.py
@author: NarraNexus
@date: 2026-03-29
@description: Create telegram_credentials table

Stores Telegram bot credentials for each Agent.
Used by TelegramTrigger for bot authentication and message polling.

Usage:
    uv run python src/xyz_agent_context/utils/database_table_management/create_telegram_credentials_table.py
    uv run python src/xyz_agent_context/utils/database_table_management/create_telegram_credentials_table.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

try:
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(project_root / "src"))
    from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager
    from xyz_agent_context.utils.database_table_management.create_table_base import (
        create_table,
        create_table_interactive,
    )


# ===== TelegramCredentialData Pydantic Model =====

class TelegramCredentialData(BaseModel):
    """
    Telegram Credential data model for table schema definition.

    Stores bot credentials — not business data.
    """
    id: Optional[int] = None
    agent_id: str = Field(..., max_length=64, description="Agent ID (primary business key)")
    bot_token: str = Field(..., max_length=256, description="Telegram bot token")
    bot_username: Optional[str] = Field(None, max_length=128, description="Bot username (cached from getMe)")
    bot_id: Optional[int] = Field(None, description="Telegram bot user ID")
    allowed_user_ids: Optional[str] = Field(None, description="JSON array of allowed Telegram user IDs")
    is_active: bool = Field(default=True, description="Whether active")
    created_at: Optional[datetime] = Field(None, description="Record creation time")
    updated_at: Optional[datetime] = Field(None, description="Last update time")


# ===== Table Manager =====

class TelegramCredentialsTableManager(BaseTableManager):
    """Table manager for telegram_credentials."""
    model = TelegramCredentialData
    table_name = "telegram_credentials"
    field_name_mapping = {"id": "id"}
    ignored_fields = {"created_at", "updated_at"}
    unique_id_field = "agent_id"


# ===== Table Creation SQL =====

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `telegram_credentials` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `agent_id` VARCHAR(64) NOT NULL COMMENT 'Agent ID (primary business key)',
    `bot_token` VARCHAR(256) NOT NULL COMMENT 'Telegram bot token',
    `bot_username` VARCHAR(128) NULL COMMENT 'Bot username (cached from getMe)',
    `bot_id` BIGINT NULL COMMENT 'Telegram bot user ID',
    `allowed_user_ids` JSON NULL COMMENT 'Allowlisted Telegram user IDs',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'Whether active',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_agent_id` (`agent_id`),
    INDEX `idx_is_active` (`is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Telegram credentials for Agents';
"""


# ===== Entry Point =====

async def create_telegram_credentials_table(force: bool = False) -> None:
    """Create the telegram_credentials table."""
    await create_table(
        table_name="telegram_credentials",
        create_sql=CREATE_TABLE_SQL,
        force=force,
    )


def main():
    parser = argparse.ArgumentParser(description="Create telegram_credentials table")
    parser.add_argument("--force", action="store_true", help="Drop and recreate if exists")
    args = parser.parse_args()

    import xyz_agent_context.settings  # noqa: F401
    asyncio.run(create_telegram_credentials_table(force=args.force))


if __name__ == "__main__":
    main()
