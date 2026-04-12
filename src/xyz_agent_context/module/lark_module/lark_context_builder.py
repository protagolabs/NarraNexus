"""
@file_name: lark_context_builder.py
@date: 2026-04-10
@description: Build execution context for Lark-triggered messages.

Inherits ChannelContextBuilderBase and implements Lark-specific data fetching
(message info, conversation history, room members).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from ._lark_credential_manager import LarkCredential
from .lark_cli_client import LarkCLIClient


class LarkContextBuilder(ChannelContextBuilderBase):
    """
    Lark-specific context builder for trigger-originated messages.

    Fetches conversation history and member info via lark-cli.
    """

    def __init__(
        self,
        event: Dict[str, Any],
        credential: LarkCredential,
        cli: LarkCLIClient,
        agent_id: str,
    ):
        self.event = event
        self.credential = credential
        self.cli = cli
        self.agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        brand_display = "Lark" if self.credential.brand == "lark" else "Feishu"
        chat_type = self.event.get("chat_type", "p2p")
        room_type = "Direct Message" if chat_type == "p2p" else "Group Room"
        return {
            "agent_id": self.agent_id,
            "channel_display_name": brand_display,
            "channel_key": "lark",
            "room_name": self.event.get("chat_name", ""),
            "room_id": self.event.get("chat_id", ""),
            "room_type": room_type,
            "sender_display_name": self.event.get("sender_name", "Unknown"),
            "sender_id": self.event.get("sender_id", ""),
            "timestamp": self.event.get("create_time", ""),
            "my_channel_id": self.credential.app_id,
            "message_body": self.event.get("content", ""),
            "send_tool_name": "lark_send_message",
        }

    async def get_conversation_history(self, limit: int) -> List[Dict]:
        chat_id = self.event.get("chat_id", "")
        if not chat_id:
            return []
        result = await self.cli.list_chat_messages(
            self.credential.profile_name,
            chat_id=chat_id,
            limit=limit,
        )
        if result.get("success"):
            data = result.get("data", {})
            # CLI returns items in various structures; normalize
            if isinstance(data, list):
                return data
            return data.get("items", data.get("messages", []))
        return []

    async def get_room_members(self) -> List[Dict]:
        # Lark CLI doesn't have a direct +chat-members shortcut.
        # Can be implemented via API layer if needed.
        return []
