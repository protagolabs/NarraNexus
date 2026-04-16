"""
@file_name: lark_module.py
@date: 2026-04-10
@description: LarkModule — Lark/Feishu integration module.

Provides MCP tools for messaging, contacts, docs, calendar, and tasks.
Each agent can bind its own Lark bot via CLI --profile isolation.

Instance level: Agent-level (one per Agent, enabled when bot is bound).
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)

from ._lark_credential_manager import LarkCredentialManager
from .lark_cli_client import LarkCLIClient


# MCP server port — must not conflict with other modules
# MessageBusModule: 7820, JobModule: 7803
LARK_MCP_PORT = 7830

# Shared CLI client (stateless)
_cli = LarkCLIClient()


async def _lark_send_to_agent(
    agent_id: str, target_id: str, message: str, **kwargs
) -> dict:
    """
    Channel sender function registered in ChannelSenderRegistry.
    Allows other modules to send Lark messages on behalf of an agent.
    """
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)
    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}
    return await _cli.send_message(cred.profile_name, user_id=target_id, text=message)


class LarkModule(XYZBaseModule):
    """
    Lark/Feishu integration module.

    Enables agents to interact with Lark: search contacts, send messages,
    create documents, manage calendar events, and handle tasks.
    """

    _sender_registered = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not LarkModule._sender_registered:
            ChannelSenderRegistry.register("lark", _lark_send_to_agent)
            LarkModule._sender_registered = True

    # =========================================================================
    # Configuration
    # =========================================================================

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="LarkModule",
            priority=6,
            enabled=True,
            description=(
                "Lark/Feishu integration: search colleagues, send messages, "
                "create documents, manage calendar, and handle tasks."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="lark_module",
            server_url=f"http://localhost:{LARK_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            from fastmcp import FastMCP

            mcp = FastMCP("LarkModule MCP")
            mcp.settings.port = LARK_MCP_PORT

            from ._lark_mcp_tools import register_lark_mcp_tools
            register_lark_mcp_tools(mcp)

            logger.info(f"LarkModule MCP server created on port {LARK_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.error(f"Failed to create LarkModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Dynamic instructions based on whether a Lark bot is bound."""
        lark_info = ctx_data.extra_data.get("lark_info")

        if not lark_info:
            return (
                "## Lark/Feishu Integration\n\n"
                "No Lark bot is bound to this agent. If the user asks how to "
                "setup or connect Lark, call `lark_setup_guide` to show the "
                "complete setup guide. To bind directly, use `lark_bind_bot` "
                "with an App ID and Secret."
            )

        brand_display = "Feishu" if lark_info.get("brand") == "feishu" else "Lark"
        bot_name = lark_info.get("bot_name", "Unknown Bot")
        auth = lark_info.get("auth_status", "not_logged_in")

        if auth in ("not_logged_in", "expired"):
            return (
                f"## Lark/Feishu Integration\n\n"
                f"Bot **{bot_name}** ({brand_display}) is bound but credentials are "
                f"{'expired' if auth == 'expired' else 'not active'}. "
                f"The user may need to re-bind the bot in the Config panel."
            )

        owner_section = ""
        owner_id = lark_info.get("owner_open_id", "")
        owner_name = lark_info.get("owner_name", "")
        if owner_id:
            owner_section = (
                f"\n**Owner identity**: {owner_name} (open_id: `{owner_id}`)\n"
                f"When the user says \"me\", \"my\", \"I\" in the context of Lark, "
                f"it refers to this person (open_id: `{owner_id}`).\n"
            )

        # Common tools section (shown for both bot_ready and user_logged_in)
        bot_tools = (
            f"### Bot identity tools (work after admin enables app permissions):\n"
            f"- **lark_send_message**: Send messages (`im:message:send_as_bot`)\n"
            f"- **lark_reply_message**: Reply to messages (`im:message:send_as_bot`)\n"
            f"- **lark_search_contacts**: Search by email/phone (`contact:user.id:readonly`)\n"
            f"- **lark_get_user_info**: Get user profile (`contact:user.base:readonly`)\n"
            f"- **lark_create_chat**: Create group chats (`im:chat`)\n"
            f"- **lark_list_chat_messages**: List messages (`im:message:readonly`)\n"
            f"- **lark_search_chat**: Search chats (`im:chat:readonly`)\n"
            f"- **lark_create_document**: Create docs (`docx:document`)\n"
            f"- **lark_fetch_document**: Read docs (`docx:document`)\n"
            f"- **lark_update_document**: Edit docs (`docx:document`)\n"
            f"- **lark_get_agenda**: View calendar (`calendar:calendar.event:read`)\n"
            f"- **lark_create_event**: Create events (`calendar:calendar.event:create`)\n"
            f"- **lark_check_freebusy**: Check availability (`calendar:calendar.free_busy:read`)\n\n"
        )

        # OAuth section (different for bot_ready vs user_logged_in)
        if auth == "bot_ready":
            oauth_section = (
                f"### User identity tools (NOT YET AVAILABLE — OAuth needed):\n"
                f"The following tools require the user to complete OAuth login first:\n"
                f"- **lark_search_contacts** (by name)\n"
                f"- **lark_search_messages**\n"
                f"- **lark_search_documents**\n\n"
                f"To unlock these, call `lark_auth_login` when the user requests one of "
                f"these features or asks to complete OAuth.\n\n"
            )
        else:  # user_logged_in
            oauth_section = (
                f"### User identity tools (AVAILABLE — OAuth completed):\n"
                f"- **lark_search_contacts** (by name): `contact:user:search`\n"
                f"- **lark_search_messages**: `search:message`\n"
                f"- **lark_search_documents**: `search:docs:read`\n\n"
            )

        rules = (
            f"**CRITICAL RULES:**\n"
            f"- Only call `lark_auth_login` when a user-identity tool fails OR the user "
            f"explicitly asks for OAuth login. NEVER call it proactively.\n"
            f"- OAuth is a TWO-STEP process:\n"
            f"  1. Call `lark_auth_login` → get verification URL + device_code\n"
            f"  2. Send the URL to the user. Explain:\n"
            f"     - If they see 'authorize' → click it, then tell you 'done'\n"
            f"     - If they see 'submit for approval' → click it, wait for admin approval, "
            f"then come back and tell you. You will send a NEW link.\n"
            f"  3. When user says done, call `lark_auth_complete` with the device_code\n"
            f"  IMPORTANT: Do NOT use Bash to run lark-cli commands. Use MCP tools only.\n"
            f"- When a Bot-identity tool fails with 'permission denied', tell the user which "
            f"app permission to enable in the Lark Open Platform admin console. Do NOT call "
            f"lark_auth_login for this — it's an app permission issue, not OAuth.\n"
            f"- When replying on Lark, call `lark_send_message` **exactly ONCE**.\n"
            f"- Do NOT reply to acknowledgments like 'ok', 'thanks', 'got it'.\n"
            f"- Use `text` parameter (plain text), NOT `markdown`.\n"
            f"- Keep replies concise. Use bullet points with emoji, not tables.\n"
            f"- If the user asks how to setup/connect Lark, call `lark_setup_guide`.\n\n"
            f"Use the lark_* tools to interact with {brand_display}."
        )

        status_label = "Bot Connected" if auth == "bot_ready" else "Fully Connected"
        return (
            f"## Lark/Feishu Integration\n\n"
            f"**{status_label}** as **{bot_name}** ({brand_display}).\n"
            f"{owner_section}\n"
            f"{bot_tools}"
            f"{oauth_section}"
            f"{rules}"
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """Inject Lark bot info into context if bound."""
        try:
            mgr = LarkCredentialManager(self.db)
            cred = await mgr.get_credential(self.agent_id)
            if cred and cred.is_active:
                lark_info = {
                    "app_id": cred.app_id,
                    "brand": cred.brand,
                    "bot_name": cred.bot_name,
                    "auth_status": cred.auth_status,
                    "profile_name": cred.profile_name,
                }
                if cred.owner_open_id:
                    lark_info["owner_open_id"] = cred.owner_open_id
                    lark_info["owner_name"] = cred.owner_name
                ctx_data.extra_data["lark_info"] = lark_info
        except Exception as e:
            logger.warning(f"LarkModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """Post-execution cleanup for Lark-triggered executions."""
        # Only process Lark-triggered executions
        ws = params.execution_ctx.working_source
        # working_source can be either the enum or its string value
        if str(ws) != WorkingSource.LARK.value:
            return
        # Future: mark messages as read, update sync state, etc.
        logger.debug(f"LarkModule after_execution for agent {params.execution_ctx.agent_id}")
