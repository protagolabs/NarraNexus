"""
@file_name: _slack_service.py
@date: 2026-05-08
@description: Shared bind/test helpers for Slack channel.

Both REST routes (`backend/routes/slack.py`) and MCP tools
(`_slack_mcp_tools.py`) call into here so bind / test logic lives in one
place. Pattern mirrors `lark_module/_lark_service.py`.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ._slack_credential_manager import SlackCredentialManager
from .slack_sdk_client import SlackSDKClient, SlackSDKError


async def do_bind(
    mgr: SlackCredentialManager,
    agent_id: str,
    bot_token: str,
    app_token: str,
    owner_email: str = "",
) -> dict[str, Any]:
    """Bind a Slack workspace to an agent. Returns ``{success, error?, data?}``."""
    return await mgr.bind(agent_id, bot_token, app_token, owner_email)


async def do_test_connection(
    mgr: SlackCredentialManager,
    agent_id: str,
) -> dict[str, Any]:
    """Verify the stored bot token still works by re-running ``auth.test``.

    This catches token revocation at the Slack side that wouldn't show up
    in a pure DB lookup. Returns the same envelope as ``do_bind``.
    """
    cred = await mgr.get(agent_id)
    if not cred:
        return {"success": False, "error": "no Slack credential bound for this agent"}

    client = SlackSDKClient(cred.bot_token)
    try:
        info = await client.auth_test()
    except SlackSDKError as e:
        logger.warning(f"[slack:{agent_id}] connection test failed: {e.code}")
        return {"success": False, "error": f"slack auth.test failed: {e.code}"}

    return {
        "success": True,
        "data": {
            "team_id": info.get("team_id", ""),
            "team_name": info.get("team", ""),
            "bot_user_id": info.get("user_id", ""),
            "bot_name": info.get("user", ""),
        },
    }
