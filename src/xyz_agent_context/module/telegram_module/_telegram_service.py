"""
@file_name: _telegram_service.py
@date: 2026-05-09
@description: Shared bind/test helpers for Telegram channel.

Both REST routes (`backend/routes/telegram.py`) and MCP tools
(`_telegram_mcp_tools.py`) call into here so bind / test logic lives in
one place. Pattern mirrors `lark_module/_lark_service.py` and
`slack_module/_slack_service.py`.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ._telegram_credential_manager import TelegramCredentialManager
from .telegram_sdk_client import TelegramSDKClient, TelegramSDKError


async def do_bind(
    mgr: TelegramCredentialManager,
    agent_id: str,
    bot_token: str,
    owner_username: str = "",
) -> dict[str, Any]:
    """Bind a Telegram bot to an agent. Returns ``{success, error?, data?}``."""
    return await mgr.bind(agent_id, bot_token, owner_username)


async def do_test_connection(
    mgr: TelegramCredentialManager,
    agent_id: str,
) -> dict[str, Any]:
    """Re-run getMe against the stored credential.

    Catches token revocation at @BotFather (``/revoke``) that wouldn't
    show up in a pure DB lookup.
    """
    cred = await mgr.get(agent_id)
    if not cred:
        return {"success": False, "error": "no Telegram credential bound for this agent"}

    client = TelegramSDKClient(cred.bot_token)
    try:
        info = await client.get_me()
    except TelegramSDKError as e:
        logger.warning(f"[telegram:{agent_id}] connection test failed: {e.code}")
        return {"success": False, "error": f"telegram getMe failed: {e.code}"}
    finally:
        await client.close()

    return {
        "success": True,
        "data": {
            "bot_user_id": str(info.get("id", "")),
            "bot_username": info.get("username", ""),
            "first_name": info.get("first_name", ""),
        },
    }
