"""
@file_name: telegram_module.py
@author: NarraNexus
@date: 2026-03-29
@description: TelegramModule — Telegram IM channel module

TelegramModule integrates the Telegram Bot API as a system-level Module.
It provides:
- MCP Tools: telegram_send_message, telegram_reply_to_message
- Hooks: data_gathering (inject Telegram context), after_execution (placeholder)
- Trigger: TelegramTrigger (separate background process for message polling)

TelegramModule is a peer of MatrixModule — it hooks into the AgentRuntime
pipeline via the standard Module lifecycle.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
)
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry

from ._telegram_hooks import telegram_hook_data_gathering, telegram_hook_after_event_execution
from ._telegram_credential_manager import TelegramCredentialManager
from ._telegram_client import TelegramBotClient


# MCP server port for Telegram tools
TELEGRAM_MCP_PORT = 7812


class TelegramModule(XYZBaseModule):
    """
    Telegram communication module.

    Enables Agents to communicate with users via Telegram Bot API.
    Provides MCP tools for sending messages and hooks for context injection.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # Class-level flag to ensure channel sender is registered only once
    _sender_registered: bool = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Register Telegram channel sender on first instantiation
        if not TelegramModule._sender_registered:
            TelegramModule.register_channel_sender()
            TelegramModule._sender_registered = True

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """Return Module configuration."""
        return ModuleConfig(
            name="TelegramModule",
            priority=5,
            enabled=True,
            description=(
                "Telegram communication channel for user-agent messaging. "
                "Provides tools for sending/receiving messages via Telegram Bot API."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """Return MCP Server configuration for Telegram tools."""
        return MCPServerConfig(
            server_name="telegram_module",
            server_url=f"http://localhost:{TELEGRAM_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        """Create MCP Server with Telegram tools registered."""
        try:
            from fastmcp import FastMCP

            mcp = FastMCP(
                "TelegramModule MCP",
            )
            mcp.settings.port = TELEGRAM_MCP_PORT

            # Register all telegram_* tools
            from ._telegram_mcp_tools import register_telegram_mcp_tools
            register_telegram_mcp_tools(mcp)

            logger.info(f"TelegramModule MCP server created on port {TELEGRAM_MCP_PORT}")
            return mcp

        except Exception as e:
            logger.error(f"Failed to create TelegramModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """
        Return Telegram-specific instructions for the system prompt.

        When credentials exist: show bot info and available tools.
        When credentials are missing: guide user to set TELEGRAM_BOT_TOKEN.
        """
        telegram_info = ctx_data.extra_data.get("telegram_info")

        tools_section = """Available tools (prefix: telegram_*):
- `telegram_register`: Register a Telegram Bot token for this agent (call when your owner provides a token)
- `telegram_send_message`: Send a message to a Telegram chat
- `telegram_reply_to_message`: Reply to a specific message in a Telegram chat"""

        if not telegram_info:
            return f"""
## Telegram Communication
You do NOT have Telegram Bot credentials configured yet.

{tools_section}

**How to set up**: When your owner provides a Telegram bot token (from @BotFather), call `telegram_register` with the token. This validates the token and saves it for your agent. The TelegramTrigger will then automatically start polling for messages.
"""

        bot_username = telegram_info.get("bot_username", "unknown")

        return f"""
## Telegram Communication

Telegram is your **user-facing messaging channel**. Use it to respond to users who message your bot, send notifications, and deliver information.

Your Telegram bot: `@{bot_username}`

### When to Use Telegram
- A user sends a message to your Telegram bot — you should reply
- You need to **send a notification** or update to a Telegram chat
- Your owner asks you to **send a message** via Telegram

{tools_section}

### Message Source Recognition
Every incoming message carries a **channel tag** (e.g., `[Telegram · username · chat_id]`).
- When you see `[Telegram · ...]` at the beginning of user input, it means this message came from Telegram, NOT from your owner
- Treat Telegram messages as **user communication** — the sender is a Telegram user interacting with your bot
- When you see `[Direct · ...]` or no channel tag, the message is from your owner via the main chat interface

### Reply Discipline
When responding to Telegram messages:
- **Use `telegram_reply_to_message`** when replying to a specific user message (preserves thread context)
- **Use `telegram_send_message`** for standalone messages or notifications
- **Stop replying** when the conversation reaches a natural end (e.g., acknowledgments, "thanks", "got it")
- **Do NOT ping-pong**: if you've answered the question and the user only acknowledges, do not reply again
- **Silence is acceptable**: not every message needs a response. Only reply when you have substantive content
"""

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject Telegram-specific context.

        Delegates to _telegram_hooks.telegram_hook_data_gathering.
        """
        try:
            return await telegram_hook_data_gathering(
                agent_id=self.agent_id,
                db=self.db,
                ctx_data=ctx_data,
            )
        except Exception as e:
            logger.error(f"TelegramModule hook_data_gathering failed: {e}")
            return ctx_data

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        Post-execution cleanup for Telegram.

        Delegates to _telegram_hooks.telegram_hook_after_event_execution.
        V1 placeholder — Telegram has no mark_read equivalent.
        """
        try:
            await telegram_hook_after_event_execution(params=params, db=self.db)
        except Exception as e:
            logger.error(f"TelegramModule hook_after_event_execution failed: {e}")

    # =========================================================================
    # Channel Sender Registration
    # =========================================================================

    @classmethod
    def register_channel_sender(cls) -> None:
        """
        Register the Telegram sender in ChannelSenderRegistry.

        Called once at module init. Enables contact_agent composite operation
        to route messages through Telegram.
        """
        async def telegram_send_to_agent(
            agent_id: str,
            target_id: str,
            message: str,
            **kwargs,
        ) -> dict:
            """
            Send a message to a target chat via Telegram.

            Args:
                agent_id: Sender agent ID
                target_id: Target Telegram chat ID
                message: Message content

            Returns:
                Result dict
            """
            from xyz_agent_context.utils import get_db_client

            db = await get_db_client()
            cred_mgr = TelegramCredentialManager(db)
            cred = await cred_mgr.get_credential(agent_id)
            if not cred:
                return {"success": False, "error": "No Telegram credentials for this agent"}

            client = TelegramBotClient(cred.bot_token)
            try:
                result = await client.send_message(chat_id=target_id, text=message)
                return {
                    "success": bool(result),
                    "chat_id": target_id,
                    "data": result,
                }
            finally:
                await client.close()

        ChannelSenderRegistry.register("telegram", telegram_send_to_agent)
