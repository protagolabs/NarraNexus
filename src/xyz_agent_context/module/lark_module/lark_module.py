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
                "setup or connect Lark, call `lark_setup` to create a new app — "
                "the user just needs to click a link.\n"
            )

        brand_display = "Feishu" if lark_info.get("brand") == "feishu" else "Lark"
        bot_name = lark_info.get("bot_name", "Unknown Bot")
        auth = lark_info.get("auth_status", "not_logged_in")

        if auth in ("not_logged_in", "expired"):
            return (
                f"## Lark/Feishu Integration\n\n"
                f"Bot **{bot_name}** ({brand_display}) is bound but credentials are "
                f"{'expired' if auth == 'expired' else 'not active'}. "
                f"The user may need to re-bind via frontend Config panel or `lark_setup`."
            )

        # Determine if this execution is from a Lark channel message
        ws = ctx_data.working_source
        is_lark_channel = (
            ws == WorkingSource.LARK
            or (isinstance(ws, str) and ws == WorkingSource.LARK.value)
        )
        logger.info(f"LarkModule.get_instructions: working_source={ws!r}, is_lark_channel={is_lark_channel}")

        # Mode indicator — this is the FIRST thing the Agent sees
        if is_lark_channel:
            mode_section = (
                "**Mode: LARK CHANNEL** — You are handling an incoming Lark message. "
                "Reply using `lark_cli im +messages-send`.\n\n"
            )
        else:
            mode_section = (
                "**Mode: OWNER CHAT** — You are in the owner's direct chat window. "
                "Reply normally as text. Do NOT use `im +messages-send` — that sends "
                "to Lark users, not to the owner's chat.\n\n"
            )

        owner_section = ""
        owner_id = lark_info.get("owner_open_id", "")
        owner_name = lark_info.get("owner_name", "")
        if owner_id:
            owner_section = (
                f"\n**Owner**: {owner_name} (open_id: `{owner_id}`)\n"
                f"When user says \"me/my/I\" in Lark context → this person.\n"
            )

        # Skill resources — list available skills for self-learning
        try:
            from ._lark_skill_loader import get_available_skills
            available = get_available_skills()
        except Exception:
            available = []

        if available:
            skill_list = ", ".join(f"`{s}`" for s in available)
            skill_section = (
                f"### How to use `lark_cli`\n\n"
                f"All commands run via `lark_cli(agent_id, command=\"...\")`. "
                f"Do NOT add `--profile` or `--format json`.\n\n"
                f"**Before using a Lark domain you haven't used this session**, "
                f"call `lark_skill(agent_id, name=\"<domain>\")` to load that "
                f"domain's SKILL.md — it teaches syntax, identity rules, and "
                f"gotchas. Skipping this step wastes turns on trial-and-error.\n"
                f"- Recommended FIRST call: `lark_skill(agent_id, \"lark-shared\")` "
                f"(auth + permissions + --as rules that apply everywhere).\n"
                f"- Available skills: {skill_list}\n"
                f"- Example: `lark_skill(agent_id, \"lark-im\")` before any `im +...`.\n"
                f"- Quick runtime help: `<domain> +<command> --help` via `lark_cli`.\n"
                f"- API field lookup: `schema <resource>` via `lark_cli` "
                f"(e.g. `schema im.messages.create`).\n\n"
            )
        else:
            skill_section = (
                "### How to use `lark_cli`\n\n"
                "All commands run via `lark_cli(agent_id, command=\"...\")`. "
                "Do NOT add `--profile` or `--format json`.\n\n"
                "Run `<domain> +<command> --help` via `lark_cli` to discover "
                "commands (e.g. `im +messages-send --help`).\n"
                "Use `schema <resource>` via `lark_cli` to check API parameters.\n\n"
            )

        # OAuth section
        if auth == "bot_ready":
            oauth_section = (
                "### OAuth Status: NOT completed\n"
                "Some commands that require user identity won't work yet.\n"
                "Only call `lark_auth` when a command fails with permission errors "
                "or the user explicitly asks for OAuth.\n\n"
            )
        else:
            oauth_section = (
                "### OAuth Status: Completed\n"
                "All commands including user-identity features are available.\n\n"
            )

        rules = (
            "### Rules\n\n"
            "**Permission error handling:**\n"
            "1. Extract the missing scope(s) from the error (e.g. `im:chat:create`)\n"
            "2. Call `lark_auth(agent_id, scopes=\"im:chat im:chat:create\")` with the specific scopes\n"
            "3. Send the verification URL to the user and explain:\n"
            "   - 'Authorize' button → click it, done\n"
            "   - 'Submit for approval' → click to request, wait for admin, then come back\n"
            "4. When user confirms → call `lark_auth_complete` with the device_code\n\n"
            "**Identity:**\n"
            "- ALWAYS add `--as bot` when sending messages, creating docs, or performing actions.\n"
            "  The CLI defaults to user identity when OAuth is completed — you must NOT impersonate the user.\n"
            "  Example: `im +messages-send --as bot --user-id ou_xxx --text \"hello\"`\n"
            "- Only use `--as user` for search/read operations that explicitly require user identity "
            "(e.g. `contact +search-user`, `im +messages-search`, `docs +search`).\n\n"
            "**General:**\n"
            "- Do NOT use Bash for lark-cli. Use `lark_cli` MCP tool only.\n"
            "- Do NOT add `--format json` to Shortcut commands (commands with `+`)\n"
            "- Only call `lark_auth` when commands fail or user asks — not preemptively.\n"
            "- `im +messages-send` sends a message to a Lark user/chat. It is NOT how you\n"
            "  reply to the owner. Only use it when the owner asks to send something to someone.\n"
        )

        status_label = "Bot Connected" if auth == "bot_ready" else "Fully Connected"
        return (
            f"## Lark/Feishu Integration\n\n"
            f"{mode_section}"
            f"**{status_label}** as **{bot_name}** ({brand_display}).\n"
            f"{owner_section}\n"
            f"{skill_section}"
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
