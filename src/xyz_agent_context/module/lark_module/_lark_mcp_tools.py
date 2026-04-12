"""
@file_name: _lark_mcp_tools.py
@date: 2026-04-10
@description: MCP tools for Lark/Feishu operations (lark_* prefix).

21 tools across 5 business domains + system management.
Each tool reads the agent's credential from DB, then delegates to LarkCLIClient.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from .lark_cli_client import LarkCLIClient
from ._lark_credential_manager import LarkCredentialManager


# Shared CLI client instance (stateless, safe to share)
_cli = LarkCLIClient()


async def _get_credential(agent_id: str):
    """Load credential from DB via MCP-level database client."""
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)
    return await mgr.get_credential(agent_id)


def register_lark_mcp_tools(mcp: Any) -> None:
    """Register all Lark MCP tools on the given FastMCP server instance."""

    # =====================================================================
    # Contact (2 tools)
    # =====================================================================

    @mcp.tool()
    async def lark_search_contacts(agent_id: str, query: str) -> dict:
        """
        Search colleagues in Lark/Feishu directory by name, email, or phone.

        Args:
            agent_id: The agent performing this action.
            query: Search keyword (name, email, or phone number).

        Returns:
            List of matching users with open_id, name, email, etc.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent. Use lark_bind_bot first."}
        return await _cli.search_user(cred.profile_name, query)

    @mcp.tool()
    async def lark_get_user_info(agent_id: str, user_id: str = "") -> dict:
        """
        Get detailed user profile info. Omit user_id to get the bot's own info.

        Args:
            agent_id: The agent performing this action.
            user_id: Target user's open_id. Empty string = bot self.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.get_user(cred.profile_name, user_id)

    # =====================================================================
    # IM — Messaging (6 tools)
    # =====================================================================

    @mcp.tool()
    async def lark_send_message(
        agent_id: str,
        chat_id: str = "",
        user_id: str = "",
        text: str = "",
        markdown: str = "",
    ) -> dict:
        """
        Send a message. Specify EITHER chat_id (group) OR user_id (direct message).
        Specify EITHER text (plain) OR markdown (rich formatting).

        Args:
            agent_id: The agent performing this action.
            chat_id: Chat ID (oc_xxx) for group chat. Mutually exclusive with user_id.
            user_id: User open_id (ou_xxx) for direct message. Mutually exclusive with chat_id.
            text: Plain text message content.
            markdown: Markdown message content (auto-wrapped as post format).
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        if not chat_id and not user_id:
            return {"success": False, "error": "Must provide either chat_id or user_id."}
        if not text and not markdown:
            return {"success": False, "error": "Must provide either text or markdown."}
        return await _cli.send_message(cred.profile_name, chat_id=chat_id, user_id=user_id, text=text, markdown=markdown)

    @mcp.tool()
    async def lark_reply_message(agent_id: str, message_id: str, text: str) -> dict:
        """
        Reply to a specific message by message_id.

        Args:
            agent_id: The agent performing this action.
            message_id: The om_ message ID to reply to.
            text: Reply text content.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.reply_message(cred.profile_name, message_id, text)

    @mcp.tool()
    async def lark_list_chat_messages(
        agent_id: str,
        chat_id: str = "",
        user_id: str = "",
        limit: int = 20,
    ) -> dict:
        """
        List recent messages in a chat or P2P conversation.

        Args:
            agent_id: The agent performing this action.
            chat_id: Chat ID (oc_xxx). Mutually exclusive with user_id.
            user_id: User open_id (ou_xxx) for P2P history. Mutually exclusive with chat_id.
            limit: Max messages to return (default 20).
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.list_chat_messages(cred.profile_name, chat_id=chat_id, user_id=user_id, limit=limit)

    @mcp.tool()
    async def lark_search_messages(agent_id: str, query: str, chat_id: str = "") -> dict:
        """
        Search messages by keyword. Optionally filter by chat_id.

        Args:
            agent_id: The agent performing this action.
            query: Search keyword.
            chat_id: Optional chat ID to narrow search scope.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.search_messages(cred.profile_name, query, chat_id=chat_id)

    @mcp.tool()
    async def lark_create_chat(agent_id: str, name: str, user_ids: str = "") -> dict:
        """
        Create a group chat and optionally invite users.

        Args:
            agent_id: The agent performing this action.
            name: Group chat name.
            user_ids: Comma-separated open_ids to invite (e.g. "ou_aaa,ou_bbb").
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        uid_list = [uid.strip() for uid in user_ids.split(",") if uid.strip()] if user_ids else None
        return await _cli.create_chat(cred.profile_name, name, user_ids=uid_list)

    @mcp.tool()
    async def lark_search_chat(agent_id: str, query: str) -> dict:
        """
        Search group chats by name or keyword.

        Args:
            agent_id: The agent performing this action.
            query: Search keyword.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.search_chat(cred.profile_name, query)

    # =====================================================================
    # Docs (4 tools)
    # =====================================================================

    @mcp.tool()
    async def lark_create_document(agent_id: str, title: str, markdown: str) -> dict:
        """
        Create a new Lark document with Markdown content.

        Args:
            agent_id: The agent performing this action.
            title: Document title.
            markdown: Document body in Markdown format.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.create_document(cred.profile_name, title, markdown)

    @mcp.tool()
    async def lark_fetch_document(agent_id: str, doc_url: str) -> dict:
        """
        Read a Lark document's content by URL.

        Args:
            agent_id: The agent performing this action.
            doc_url: Document URL (from Lark share link or search result).
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.fetch_document(cred.profile_name, doc_url)

    @mcp.tool()
    async def lark_update_document(agent_id: str, doc_url: str, markdown: str) -> dict:
        """
        Update an existing Lark document with new Markdown content.

        Args:
            agent_id: The agent performing this action.
            doc_url: Document URL to update.
            markdown: New content in Markdown format.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.update_document(cred.profile_name, doc_url, markdown)

    @mcp.tool()
    async def lark_search_documents(agent_id: str, query: str) -> dict:
        """
        Search documents, Wiki pages, and spreadsheets by keyword.

        Args:
            agent_id: The agent performing this action.
            query: Search keyword.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.search_documents(cred.profile_name, query)

    # =====================================================================
    # Calendar (3 tools)
    # =====================================================================

    @mcp.tool()
    async def lark_get_agenda(agent_id: str, date: str = "") -> dict:
        """
        View calendar agenda. Defaults to today.

        Args:
            agent_id: The agent performing this action.
            date: Date in YYYY-MM-DD format. Empty = today.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.get_agenda(cred.profile_name, date)

    @mcp.tool()
    async def lark_create_event(
        agent_id: str,
        summary: str,
        start: str,
        end: str,
        attendees: str = "",
    ) -> dict:
        """
        Create a calendar event and optionally invite attendees.

        Args:
            agent_id: The agent performing this action.
            summary: Event title/summary.
            start: Start time, e.g. "2026-04-15 14:00".
            end: End time, e.g. "2026-04-15 15:00".
            attendees: Comma-separated open_ids to invite (e.g. "ou_aaa,ou_bbb").
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        att_list = [a.strip() for a in attendees.split(",") if a.strip()] if attendees else None
        return await _cli.create_event(cred.profile_name, summary, start, end, attendees=att_list)

    @mcp.tool()
    async def lark_check_freebusy(
        agent_id: str, user_ids: str, start: str, end: str
    ) -> dict:
        """
        Check free/busy status for one or more users in a time range.

        Args:
            agent_id: The agent performing this action.
            user_ids: Comma-separated open_ids (e.g. "ou_aaa,ou_bbb").
            start: Range start, e.g. "2026-04-15 09:00".
            end: Range end, e.g. "2026-04-15 18:00".
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        uid_list = [u.strip() for u in user_ids.split(",") if u.strip()]
        if not uid_list:
            return {"success": False, "error": "Must provide at least one user_id."}
        return await _cli.freebusy(cred.profile_name, uid_list, start, end)

    # =====================================================================
    # Task (3 tools)
    # =====================================================================

    @mcp.tool()
    async def lark_create_task(
        agent_id: str, summary: str, due: str = "", description: str = ""
    ) -> dict:
        """
        Create a task.

        Args:
            agent_id: The agent performing this action.
            summary: Task title/summary.
            due: Due date, e.g. "2026-04-15 17:00". Optional.
            description: Task description. Optional.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.create_task(cred.profile_name, summary, due=due, description=description)

    @mcp.tool()
    async def lark_get_my_tasks(agent_id: str) -> dict:
        """
        List all tasks assigned to the logged-in user.

        Args:
            agent_id: The agent performing this action.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.get_my_tasks(cred.profile_name)

    @mcp.tool()
    async def lark_complete_task(agent_id: str, task_id: str) -> dict:
        """
        Mark a task as complete.

        Args:
            agent_id: The agent performing this action.
            task_id: The task ID to complete.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        return await _cli.complete_task(cred.profile_name, task_id)

    # =====================================================================
    # System — Bot Management (3 tools)
    # =====================================================================

    @mcp.tool()
    async def lark_bind_bot(
        agent_id: str, app_id: str, app_secret: str, brand: str = "feishu"
    ) -> dict:
        """
        Bind a Lark/Feishu bot to this agent. This registers a CLI profile
        and stores the credential.

        Args:
            agent_id: The agent to bind.
            app_id: Feishu/Lark App ID (e.g. "cli_xxx").
            app_secret: App Secret from Feishu Open Platform.
            brand: "feishu" (China, default) or "lark" (International).
        """
        from ._lark_credential_manager import LarkCredential

        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        profile_name = f"agent_{agent_id}"

        # Register CLI profile
        result = await _cli.config_init(profile_name, app_id, app_secret, brand)
        if not result.get("success"):
            return result

        # Save credential to DB
        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)
        cred = LarkCredential(
            agent_id=agent_id,
            app_id=app_id,
            app_secret_ref=f"appsecret:{app_id}",
            brand=brand,
            profile_name=profile_name,
        )
        await mgr.save_credential(cred)

        logger.info(f"Lark bot bound: agent={agent_id}, app_id={app_id}, brand={brand}")
        return {"success": True, "data": {"profile_name": profile_name, "message": "Bot bound. Now call lark_auth_login to complete OAuth."}}

    @mcp.tool()
    async def lark_auth_login(agent_id: str) -> dict:
        """
        Initiate OAuth login for the bound Lark bot.
        Returns an authorization URL that the user must open in a browser.

        Args:
            agent_id: The agent whose bot to log in.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent. Use lark_bind_bot first."}
        return await _cli.auth_login(cred.profile_name)

    @mcp.tool()
    async def lark_auth_status(agent_id: str) -> dict:
        """
        Check the authentication status of the bound Lark bot.

        Args:
            agent_id: The agent to check.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        result = await _cli.auth_status(cred.profile_name)

        # Update DB auth status if we can determine it
        # Bot identity ("identity": "bot") is always valid — don't require user login
        if result.get("success"):
            data = result.get("data", {})
            identity = data.get("identity", "")
            users = data.get("users", "(no logged-in users)")
            if identity == "bot" or users != "(no logged-in users)":
                new_status = "logged_in"
            else:
                new_status = "not_logged_in"
            if new_status != cred.auth_status:
                db = await XYZBaseModule.get_mcp_db_client()
                mgr = LarkCredentialManager(db)
                await mgr.update_auth_status(agent_id, new_status)

        return result
