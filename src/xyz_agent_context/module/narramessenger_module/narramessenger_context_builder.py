"""
@file_name: narramessenger_context_builder.py
@date: 2026-06-17
@description: Build execution context for NarraMessenger-triggered messages.

Inherits ChannelContextBuilderBase. Unlike Telegram (which keeps its own
``bus_messages`` log), NarraMessenger ships conversation context INLINE in
every invocation:
  - DM:    ``context`` = recent ``[{role, sender, content}]`` (up to ~20).
  - Group: ``group_context.history_messages`` + ``compressed_context`` +
           ``members`` (with role / identity_badge / governance_status).

So ``get_conversation_history`` / ``get_room_members`` read straight from
``ParsedMessage.raw`` (the full invocation), no extra API call.
"""

from __future__ import annotations

from typing import Any, Dict, List

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage

from ._narramessenger_credential_manager import NarramessengerCredential


class NarramessengerContextBuilder(ChannelContextBuilderBase):
    """NarraMessenger-specific context builder."""

    def __init__(
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        agent_id: str,
    ):
        self._message = message
        self._credential = credential
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        room_id = self._message.chat_id
        is_group = self._message.chat_type == ChatType.GROUP

        gc = (self._message.raw or {}).get("group_context") or {}
        room_name = ((gc.get("room") or {}).get("name")) or ""

        reply_instruction = (
            'call `narra_reply(text="YOUR_REPLY")` — your reply is delivered '
            "to this NarraMessenger room automatically. Send exactly ONE "
            "message with your real answer (plain text / markdown). The room "
            "stays quiet until you call this tool — there is no intermediate "
            "status message, so if the work takes a while, just do it and send "
            "the final answer when ready. To attach an image or file, put it "
            "in your workspace first, then call "
            f'`narra_send_media(room_id="{room_id}", file_path="...")`.'
        )

        return {
            "agent_id": self._agent_id,
            "channel_display_name": "NarraMessenger",
            "channel_key": "narramessenger",
            "room_name": room_name,
            "room_id": room_id,
            "room_type": "Group Room" if is_group else "Direct Message",
            "sender_display_name": self._message.sender_name or self._message.sender_id,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": self._credential.matrix_user_id,
            "message_body": self._message.content,
            "send_tool_name": "narra_reply",
            "reply_instruction": reply_instruction,
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """Always returns an empty list — history is served from memory.

        Pre-Matrix (Gateway/polling) NarraMessenger shipped inline
        ``group_context.history_messages`` (group) or ``context``
        (DM) in every invocation, and this method normalised them into
        the base's ``[{sender, timestamp, body}]`` shape so
        ``## Conversation History`` in the channel prompt could carry a
        few rounds of context.

        Direct Matrix (Commit 7, 2026-07-02) removed that channel: we
        read raw ``m.room.message`` events off ``/sync`` and neither
        field is populated in ``ParsedMessage.raw``. Every read fell
        through to the empty fallback, and the section was recomputed
        as ``""`` on every turn.

        The intended path is now ChatModule's chat_history: past turns
        (including their attachments) are recalled from persisted
        messages during ``hook_data_gathering`` and rendered into the
        system prompt, not into the channel prompt. Historical
        attachment markers are synthesised there via
        ``Attachment.synthesize_marker``; the current turn's marker is
        appended by
        ``ChannelContextBuilderBase.with_current_turn_attachments``.
        """
        del limit  # honoured only as a signature contract
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        """Group member list from ``group_context.members``. DM → empty (the
        base hides the members section for <=2 members anyway)."""
        gc = (self._message.raw or {}).get("group_context") or {}
        members = gc.get("members")
        if not isinstance(members, list):
            return []
        out: List[Dict[str, Any]] = []
        for m in members:
            if not isinstance(m, dict):
                continue
            uid = m.get("matrix_user_id", "") or ""
            if not uid:
                continue
            out.append({
                "user_id": uid,
                "display_name": m.get("display_name", "") or uid,
            })
        return out
