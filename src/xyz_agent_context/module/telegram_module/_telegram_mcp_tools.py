"""
@file_name: _telegram_mcp_tools.py
@author: NarraNexus
@date: 2026-03-29
@description: MCP atomic tools for Telegram operations (telegram_* prefix)

These tools map directly to Telegram Bot API endpoints.
The telegram_ prefix avoids collision with matrix_*, slack_*, etc.

Each tool creates its own TelegramBotClient from the credential stored
in the database. The agent_id is used to look up credentials.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def register_telegram_mcp_tools(mcp: Any) -> None:
    """
    Register all Telegram MCP tools on the given MCP server instance.

    Called by TelegramModule.create_mcp_server().

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool()
    async def telegram_register(
        agent_id: str,
        bot_token: str,
    ) -> dict:
        """
        Register (or re-register) a Telegram Bot for this agent.

        Call this tool when your owner provides a Telegram bot token.
        The token is validated via the Telegram API (getMe) and stored
        per-agent in the database.

        Args:
            agent_id: Your agent ID
            bot_token: Telegram bot token from @BotFather (e.g. "123456789:ABCdef...")

        Returns:
            Result dict with bot username on success, or error details
        """
        client = None
        try:
            from ._telegram_client import TelegramBotClient
            from ._telegram_credential_manager import TelegramCredentialManager, TelegramCredential
            from xyz_agent_context.module.base import XYZBaseModule

            # Validate the token
            client = TelegramBotClient(bot_token)
            me = await client.get_me()
            bot_username = me.get("username", "")
            bot_id = me.get("id", 0)

            if not bot_username:
                return {"success": False, "error": "Token is valid but getMe returned no username"}

            # Save credential
            db = await XYZBaseModule.get_mcp_db_client()
            cred_mgr = TelegramCredentialManager(db)
            cred = TelegramCredential(
                agent_id=agent_id,
                bot_token=bot_token,
                bot_username=bot_username,
                bot_id=bot_id,
                is_active=True,
            )
            await cred_mgr.save_credential(cred)

            logger.info(f"Telegram bot registered for agent {agent_id}: @{bot_username}")
            return {
                "success": True,
                "bot_username": bot_username,
                "bot_id": bot_id,
                "message": f"Telegram bot @{bot_username} registered successfully. "
                           f"The TelegramTrigger will start polling for messages automatically.",
            }
        except Exception as e:
            logger.error(f"telegram_register failed for agent {agent_id}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client:
                await client.close()

    @mcp.tool()
    async def telegram_send_message(
        agent_id: str,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
    ) -> dict:
        """
        Send a text message to a Telegram chat.

        Use this tool to send messages or reply to conversations in Telegram chats.

        Args:
            agent_id: Your agent ID (for credential lookup)
            chat_id: Target chat ID (e.g. "123456789" or "-1001234567890")
            text: Message text to send
            parse_mode: Parse mode for formatting ("HTML", "Markdown", or "")

        Returns:
            Result dict with message data on success, or error details
        """
        client = None
        try:
            cred = await _get_credential(agent_id)
            if not cred:
                return {"success": False, "error": "Telegram credentials not found for this agent"}

            from ._telegram_client import TelegramBotClient
            client = TelegramBotClient(cred.bot_token)
            result = await client.send_message(chat_id, text, parse_mode=parse_mode)

            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"telegram_send_message failed for agent {agent_id}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client:
                await client.close()

    @mcp.tool()
    async def telegram_reply_to_message(
        agent_id: str,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
    ) -> dict:
        """
        Reply to a specific message in a Telegram chat.

        Use this tool to reply directly to a particular message in a Telegram chat.
        The reply will be visually linked to the original message.

        Args:
            agent_id: Your agent ID (for credential lookup)
            chat_id: Target chat ID (e.g. "123456789" or "-1001234567890")
            message_id: ID of the message to reply to
            text: Reply text to send
            parse_mode: Parse mode for formatting ("HTML", "Markdown", or "")

        Returns:
            Result dict with message data on success, or error details
        """
        client = None
        try:
            cred = await _get_credential(agent_id)
            if not cred:
                return {"success": False, "error": "Telegram credentials not found for this agent"}

            from ._telegram_client import TelegramBotClient
            client = TelegramBotClient(cred.bot_token)
            result = await client.send_message(
                chat_id, text, reply_to_message_id=message_id, parse_mode=parse_mode
            )

            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"telegram_reply_to_message failed for agent {agent_id}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if client:
                await client.close()


async def _get_credential(agent_id: str):
    """
    Helper: look up Telegram credentials for the given agent.

    Returns:
        TelegramCredential, or None if not found
    """
    from xyz_agent_context.module.base import XYZBaseModule
    from ._telegram_credential_manager import TelegramCredentialManager

    try:
        db = await XYZBaseModule.get_mcp_db_client()
        cred_mgr = TelegramCredentialManager(db)
        cred = await cred_mgr.get_credential(agent_id)
        return cred
    except Exception as e:
        logger.error(f"Failed to get Telegram credential for agent {agent_id}: {e}")
        return None
