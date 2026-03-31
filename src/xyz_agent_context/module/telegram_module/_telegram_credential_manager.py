"""
@file_name: _telegram_credential_manager.py
@author: NarraNexus
@date: 2026-03-29
@description: Telegram credential management

Manages Agent credentials for Telegram Bot API:
- Store/retrieve credentials from the telegram_credentials table
- Validate bot tokens via getMe
- Track allowed user IDs for access control

This is a private implementation — external code should access it via TelegramModule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.utils import DatabaseClient


@dataclass
class TelegramCredential:
    """
    A single Agent's Telegram Bot credentials.

    Stored in the telegram_credentials table. Used by TelegramTrigger
    for authentication and access control.
    """
    agent_id: str
    bot_token: str = ""
    bot_username: str = ""
    bot_id: int = 0
    allowed_user_ids: list = field(default_factory=list)
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TelegramCredentialManager:
    """
    CRUD operations for Telegram credentials.

    Works with the telegram_credentials table in the NarraNexus database.
    """

    TABLE = "telegram_credentials"

    def __init__(self, db: DatabaseClient):
        self.db = db

    async def get_credential(self, agent_id: str) -> Optional[TelegramCredential]:
        """
        Get a single Agent's Telegram credential.

        Args:
            agent_id: Agent ID

        Returns:
            TelegramCredential, or None if not found
        """
        row = await self.db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_credential(row)

    async def get_all_active(self) -> List[TelegramCredential]:
        """
        Get all active credentials (for TelegramTrigger polling).

        Returns:
            List of active TelegramCredential instances
        """
        query = f"SELECT * FROM {self.TABLE} WHERE is_active = TRUE"
        rows = await self.db.execute(query)
        return [self._row_to_credential(r) for r in rows]

    async def save_credential(self, cred: TelegramCredential) -> None:
        """
        Insert or update a credential (upsert).

        Args:
            cred: TelegramCredential to save
        """
        now = datetime.now(timezone.utc)
        data = {
            "agent_id": cred.agent_id,
            "bot_token": cred.bot_token,
            "bot_username": cred.bot_username,
            "bot_id": cred.bot_id,
            "allowed_user_ids": json.dumps(cred.allowed_user_ids),
            "is_active": cred.is_active,
            "updated_at": now,
        }

        existing = await self.db.get_one(self.TABLE, {"agent_id": cred.agent_id})
        if existing:
            await self.db.update(self.TABLE, {"agent_id": cred.agent_id}, data)
        else:
            data["created_at"] = now
            await self.db.insert(self.TABLE, data)

        logger.debug(f"Saved Telegram credential for agent {cred.agent_id}")

    async def deactivate(self, agent_id: str) -> None:
        """
        Mark a credential as inactive (stop polling).

        Args:
            agent_id: Agent ID
        """
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"is_active": False, "updated_at": datetime.now(timezone.utc)},
        )

    @staticmethod
    def _row_to_credential(row: Dict[str, Any]) -> TelegramCredential:
        """Convert a database row to a TelegramCredential dataclass."""
        # allowed_user_ids is stored as JSON in the DB
        raw_allowed = row.get("allowed_user_ids", "[]")
        if isinstance(raw_allowed, str):
            try:
                allowed = json.loads(raw_allowed)
            except (json.JSONDecodeError, TypeError):
                allowed = []
        elif isinstance(raw_allowed, list):
            allowed = raw_allowed
        else:
            allowed = []

        return TelegramCredential(
            agent_id=row.get("agent_id", ""),
            bot_token=row.get("bot_token", ""),
            bot_username=row.get("bot_username", ""),
            bot_id=int(row.get("bot_id", 0)),
            allowed_user_ids=allowed,
            is_active=bool(row.get("is_active", True)),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


async def ensure_telegram_credential(
    db: DatabaseClient,
    agent_id: str,
) -> Optional[TelegramCredential]:
    """
    Ensure a Telegram credential exists for the given agent and return it.

    Flow:
    1. Check DB for existing credential — return immediately if found
    2. Read bot token from settings — return None if empty
    3. Validate token via getMe to obtain bot_username and bot_id
    4. Parse allowed_user_ids from settings (comma-separated)
    5. Save and return the new credential

    Args:
        db: Database client
        agent_id: Agent ID

    Returns:
        TelegramCredential on success, None if no token configured or validation fails
    """
    from ._telegram_client import TelegramBotClient

    cred_mgr = TelegramCredentialManager(db)

    # 1. Check existing
    existing = await cred_mgr.get_credential(agent_id)
    if existing:
        return existing

    # 2. Read token from settings
    try:
        from xyz_agent_context.settings import settings
        bot_token = settings.telegram_bot_token
    except Exception as e:
        logger.warning(f"Failed to read telegram_bot_token from settings: {e}")
        return None

    if not bot_token:
        logger.debug(f"No telegram_bot_token configured, skipping Telegram setup for {agent_id}")
        return None

    # 3. Validate via getMe
    client = TelegramBotClient(bot_token)
    try:
        me = await client.get_me()
        bot_username = me.get("username", "")
        bot_id = me.get("id", 0)

        if not bot_username:
            logger.warning(f"getMe returned no username for agent {agent_id}")
            return None

        logger.info(f"Telegram bot validated: @{bot_username} (id={bot_id})")
    except Exception as e:
        logger.error(f"Failed to validate Telegram bot token for agent {agent_id}: {e}")
        return None
    finally:
        await client.close()

    # 4. Parse allowed_user_ids
    allowed_user_ids: list[int] = []
    try:
        from xyz_agent_context.settings import settings
        raw_ids = settings.telegram_allowed_user_ids
        if raw_ids:
            allowed_user_ids = [int(uid.strip()) for uid in raw_ids.split(",") if uid.strip()]
    except Exception as e:
        logger.warning(f"Failed to parse telegram_allowed_user_ids: {e}")

    # 5. Save credential
    cred = TelegramCredential(
        agent_id=agent_id,
        bot_token=bot_token,
        bot_username=bot_username,
        bot_id=bot_id,
        allowed_user_ids=allowed_user_ids,
        is_active=True,
    )
    await cred_mgr.save_credential(cred)
    logger.info(f"Saved Telegram credential for agent {agent_id}: @{bot_username}")

    return cred
