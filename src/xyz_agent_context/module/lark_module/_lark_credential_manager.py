"""
@file_name: _lark_credential_manager.py
@date: 2026-04-10
@description: CRUD operations for lark_credentials table.

Stores per-agent Lark/Feishu bot binding information. The actual App Secret
is managed by lark-cli in the system Keychain — we only store a reference ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from loguru import logger


@dataclass
class LarkCredential:
    """One agent's Lark bot binding."""

    agent_id: str
    app_id: str
    app_secret_ref: str  # Keychain reference, e.g. "appsecret:cli_xxx"
    brand: str  # "feishu" or "lark"
    profile_name: str  # CLI profile name, e.g. "agent_{agent_id}"
    bot_name: str = ""
    owner_open_id: str = ""  # Lark open_id of the agent's owner
    owner_name: str = ""  # Display name of the owner
    auth_status: str = "not_logged_in"  # not_logged_in / logged_in / expired
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class LarkCredentialManager:
    """CRUD for lark_credentials table."""

    TABLE = "lark_credentials"

    def __init__(self, db):
        self.db = db

    def _row_to_credential(self, row: dict) -> LarkCredential:
        return LarkCredential(
            agent_id=row["agent_id"],
            app_id=row["app_id"],
            app_secret_ref=row.get("app_secret_ref", ""),
            brand=row.get("brand", "feishu"),
            profile_name=row.get("profile_name", ""),
            bot_name=row.get("bot_name", ""),
            owner_open_id=row.get("owner_open_id", ""),
            owner_name=row.get("owner_name", ""),
            auth_status=row.get("auth_status", "not_logged_in"),
            is_active=bool(row.get("is_active", True)),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    async def get_credential(self, agent_id: str) -> Optional[LarkCredential]:
        """Get credential for a single agent."""
        row = await self.db.get_one(self.TABLE, {"agent_id": agent_id})
        if not row:
            return None
        return self._row_to_credential(row)

    async def get_by_app_id(self, app_id: str) -> List[LarkCredential]:
        """Get all credentials using a specific App ID."""
        rows = await self.db.get(self.TABLE, {"app_id": app_id})
        return [self._row_to_credential(r) for r in rows]

    async def get_active_credentials(self) -> List[LarkCredential]:
        """Get all active and logged-in credentials (for trigger startup)."""
        rows = await self.db.get(
            self.TABLE,
            {"is_active": 1, "auth_status": "logged_in"},
        )
        return [self._row_to_credential(r) for r in rows]

    async def save_credential(self, cred: LarkCredential) -> None:
        """Insert or update a credential."""
        data = {
            "agent_id": cred.agent_id,
            "app_id": cred.app_id,
            "app_secret_ref": cred.app_secret_ref,
            "brand": cred.brand,
            "profile_name": cred.profile_name,
            "bot_name": cred.bot_name,
            "owner_open_id": cred.owner_open_id,
            "owner_name": cred.owner_name,
            "auth_status": cred.auth_status,
            "is_active": 1 if cred.is_active else 0,
        }
        existing = await self.get_credential(cred.agent_id)
        if existing:
            await self.db.update(self.TABLE, {"agent_id": cred.agent_id}, data)
        else:
            await self.db.insert(self.TABLE, data)
        logger.info(f"Saved Lark credential for agent {cred.agent_id} (app_id={cred.app_id})")

    async def update_auth_status(self, agent_id: str, status: str) -> None:
        """Update authentication status."""
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"auth_status": status},
        )

    async def update_bot_name(self, agent_id: str, bot_name: str) -> None:
        """Update bot display name (after successful auth)."""
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"bot_name": bot_name},
        )

    async def update_owner(self, agent_id: str, open_id: str, name: str) -> None:
        """Update owner Lark identity."""
        await self.db.update(
            self.TABLE,
            {"agent_id": agent_id},
            {"owner_open_id": open_id, "owner_name": name},
        )

    async def delete_credential(self, agent_id: str) -> None:
        """Delete credential for an agent."""
        await self.db.delete(self.TABLE, {"agent_id": agent_id})
        logger.info(f"Deleted Lark credential for agent {agent_id}")
