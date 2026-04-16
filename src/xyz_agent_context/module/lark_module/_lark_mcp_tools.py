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

    # NOTE: Task tools (lark_create_task, lark_get_my_tasks, lark_complete_task)
    # are removed — requires task:task:write/read permissions not commonly enabled.

    # =====================================================================
    # System — Setup & Bot Management
    # =====================================================================

    @mcp.tool()
    async def lark_setup_guide(agent_id: str) -> dict:
        """
        Return the complete Lark/Feishu setup guide for new users.
        Call this when the user asks how to connect/bind/setup Lark.

        Args:
            agent_id: The agent performing this action.
        """
        guide = """# Lark/Feishu Bot Setup Guide

## Step 1: Create Application
1. Go to Lark Open Platform: https://open.larksuite.com/app (International) or https://open.feishu.cn/app (China)
2. Click "Create Custom App"
3. Fill in app name and description
4. Note down your **App ID** (cli_xxx) and **App Secret**

## Step 2: Enable Bot
1. In your app settings → Features → Bot → Enable

## Step 3: Add App Permissions (Application Identity)
Go to Permissions Management → search and enable these under **Application Identity**:

**Required (core messaging):**
- `im:message:send_as_bot` — Send messages as bot
- `contact:user.id:readonly` — Look up users by email/phone
- `contact:user.base:readonly` — Get user profile info

**Recommended (full features):**
- `im:message:readonly` — Read chat messages
- `im:chat:readonly` — Search/view group chats
- `im:chat` — Create group chats
- `docx:document` — Create/read/edit documents
- `calendar:calendar.event:read` — View calendar
- `calendar:calendar.event:create` — Create events
- `calendar:calendar.free_busy:read` — Check availability

## Step 4: Add User Permissions (User Identity)
These enable features that require acting as a specific user:
- `contact:user:search` — Search colleagues by name
- `search:message` — Search message history
- `search:docs:read` — Search documents
- `offline_access` — Keep authorization active

## Step 5: Subscribe to Events
1. Go to Events & Callbacks → Event Configuration
2. Select **Long Connection** (WebSocket) mode
3. Add event: `im.message.receive_v1` (Receive messages)
4. Enable: "Read direct messages sent to bot"

## Step 6: Set App Availability
1. Go to App Availability / Availability
2. Change from "specific members" to "all employees" (or add specific people)

## Step 7: Publish & Approve
1. Go to Version Management → Create Version
2. Fill in version number and update notes
3. Submit for approval
4. Wait for admin to approve

## Step 8: Bind to NarraNexus
After approval, tell your Agent ALL 4 pieces of info (all are required):
1. **App ID** (starts with cli_)
2. **App Secret**
3. **Your Lark/Feishu email** (REQUIRED — without this the Agent won't know who you are)
4. **Platform**: **Feishu** (China, feishu.cn) or **Lark** (International, larksuite.com)

Example: "Bind my Lark bot: App ID cli_xxx, Secret xxx, email xxx@company.com, platform Lark"

⚠️ The email MUST be provided — it links your Lark identity to the Agent so it knows "me" = you.

## Step 9: User OAuth (for user-identity features)
Some features (search by name, search messages, search documents) need user authorization.
Ask your Agent: "Help me complete Lark OAuth login"
- If you see "authorize" → just click to complete. Done!
- If you see "submit for approval" → click it to request permissions, wait for admin approval.
  After approval, come back and ask the Agent again — it will send a new link for authorization.

That's it! Your Agent can now use Lark/Feishu."""

        return {"success": True, "data": {"guide": guide}}

    @mcp.tool()
    async def lark_bind_bot(
        agent_id: str, app_id: str, app_secret: str,
        brand: str = "feishu", owner_email: str = ""
    ) -> dict:
        """
        Bind a Lark/Feishu bot to this agent. This registers a CLI profile
        and stores the credential.

        IMPORTANT: Always ask the user for their email so the Agent knows
        who the owner is. Without email, features like "check my calendar"
        won't know whose calendar to check.

        Args:
            agent_id: The agent to bind.
            app_id: Feishu/Lark App ID (e.g. "cli_xxx").
            app_secret: App Secret from Feishu Open Platform.
            brand: "feishu" (China, default) or "lark" (International).
            owner_email: Owner's Lark/Feishu email to link their identity.
        """
        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        if not owner_email:
            return {
                "success": False,
                "error": "owner_email is required. Ask the user for their Lark/Feishu email so the Agent knows who they are."
            }

        db = await XYZBaseModule.get_mcp_db_client()
        mgr = LarkCredentialManager(db)

        # Reuse shared bind logic from service layer
        from ._lark_service import do_bind
        result = await do_bind(mgr, agent_id, app_id, app_secret, brand, owner_email)

        if result.get("success"):
            logger.info(f"Lark bot bound via MCP: agent={agent_id}, app_id={app_id}, brand={brand}, owner={owner_email}")

        return result

    @mcp.tool()
    async def lark_auth_login(agent_id: str) -> dict:
        """
        Initiate OAuth login for the bound Lark bot. Returns an authorization
        URL that the user must open in a browser to complete login.

        ONLY call this when:
        - A user-identity tool fails with 'needs OAuth' or 'missing scope'
        - The user explicitly asks to complete Lark OAuth login

        Do NOT call this proactively or repeatedly. This uses safe scopes
        that won't trigger re-approval.

        Args:
            agent_id: The agent whose bot to log in.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent. Use lark_bind_bot first."}
        result = await _cli.auth_login(cred.profile_name)
        if result.get("success"):
            data = result.get("data", {})
            device_code = data.get("device_code", "")
            if device_code:
                result["data"]["next_step"] = (
                    "After user completes authorization in browser, "
                    "call lark_auth_complete with this device_code to finish login."
                )
        return result

    @mcp.tool()
    async def lark_auth_complete(agent_id: str, device_code: str) -> dict:
        """
        Complete OAuth login after user has authorized in browser.
        Call this AFTER the user confirms they clicked the authorization link.

        Args:
            agent_id: The agent whose bot is being authorized.
            device_code: The device_code returned by lark_auth_login.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound to this agent."}
        result = await _cli.auth_login_complete(cred.profile_name, device_code)
        if result.get("success"):
            # Update auth status in DB
            db = await XYZBaseModule.get_mcp_db_client()
            mgr = LarkCredentialManager(db)
            from ._lark_credential_manager import AUTH_STATUS_USER_LOGGED_IN
            await mgr.update_auth_status(agent_id, AUTH_STATUS_USER_LOGGED_IN)
        return result
