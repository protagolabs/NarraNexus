"""
@file_name: telegram_trigger.py
@date: 2026-05-09
@description: Telegram channel trigger built on ``ChannelTriggerBase``.

Uses long-poll (``getUpdates`` with timeout=30) — no webhook, no public IP.
Each active credential gets one long-poll loop. Events flow through the
shared dedup → debounce → worker pipeline owned by the base class.

Telegram-specific concerns this class handles:
  - **Privacy mode default ON (we KEEP it that way)**: bot in groups
    only sees @-mentions and ``/commands``. This is the **right**
    default — it gives us the @-mention-only group behaviour Slack is
    still trying to retrofit (Phase 5). ``_NO_BOT_INSTRUCTION``
    explicitly tells users NOT to disable it. Trigger does no extra
    filtering for groups beyond what Telegram already does at source.
  - **getUpdates / setWebhook exclusivity**: 409 Conflict if a webhook
    is set. ``deleteWebhook`` runs once at bind time; if a 409 still
    happens at runtime, we call it again and retry.
  - **chat_id is signed int64**: positive=user DM, negative=group,
    very-large-negative=supergroup/channel. Stored as string everywhere.
  - **forum topic threads**: supergroup forums have ``message_thread_id``;
    we map to ``ParsedMessage.thread_id`` so reply-in-thread works.
  - **No conversation history API**: ``get_conversation_history`` in
    the context builder returns ``[]`` — agent relies on ChatModule.

The base class owns: dedup, worker pool, credential watcher, audit log,
inbox writer, reconnect backoff. This class overrides the abstract
surface (connect/parse/echo/sender/builder/load) only, plus
``extract_output`` to scrape ``tg_cli`` tool-call args (NOT
``output_text`` — Phase 3 Slack regression).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Optional

from loguru import logger

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)

from ._telegram_credential_manager import TelegramCredential, TelegramCredentialManager
from .telegram_context_builder import TelegramContextBuilder
from .telegram_sdk_client import TelegramSDKClient, TelegramSDKError


class TelegramTrigger(ChannelTriggerBase):
    """Telegram channel trigger.

    One long-poll generator per credential; the base feeds events into
    the shared queue → workers.
    """

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "telegram"
    brand_display = "Telegram"
    working_source = WorkingSource.TELEGRAM

    # ── Worker pool ──────────────────────────────────────────────────────
    MIN_WORKERS = 3
    WORKERS_PER_SUBSCRIBER = 2
    MAX_WORKERS = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800
    CLEANUP_INTERVAL_SECONDS = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS = 600
    DEDUP_RETENTION_DAYS = 7
    AUDIT_RETENTION_DAYS = 30

    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # Telegram users burst-message often; same as Slack.
    DEBOUNCE_WINDOW_MS = 1500

    # Long-poll tuning
    POLL_TIMEOUT_SECONDS = 30
    POLL_IDLE_SLEEP_SECONDS = 0.5

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            history_config=ChannelHistoryConfig(
                load_conversation_history=False,  # Telegram has no history API
                history_limit=0,
                history_max_chars=0,
            ),
        )
        # Per-credential long-poll offset (last consumed update_id + 1)
        self._poll_offsets: dict[str, int] = {}
        # Per-credential SDK client kept so stop() can close cleanly
        self._sdk_clients: dict[str, TelegramSDKClient] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        await super().start(db)
        logger.info(
            f"TelegramTrigger started: {len(self._workers)} workers, "
            f"watching channel_telegram_credentials for active rows"
        )

    async def stop(self) -> None:
        for key, client in list(self._sdk_clients.items()):
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[telegram:{key}] client close during stop: {e}")
        self._sdk_clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(self) -> list[TelegramCredential]:
        if not self._db:
            return []
        mgr = TelegramCredentialManager(self._db)
        return await mgr.list_active()

    def _subscriber_key(self, credential: TelegramCredential) -> str:  # type: ignore[override]
        return credential.agent_id

    async def connect(
        self, credential: TelegramCredential
    ) -> AsyncIterator[dict]:
        """Long-poll loop. Yields raw Telegram update dicts.

        On 409 Conflict (webhook still set), call ``deleteWebhook`` once
        and retry. Other ``TelegramSDKError`` raises so the base class
        backs off and reconnects.
        """
        client = TelegramSDKClient(credential.bot_token)
        key = self._subscriber_key(credential)
        self._sdk_clients[key] = client
        offset = self._poll_offsets.get(key, 0)

        logger.info(
            f"[telegram:{credential.agent_id}] long-poll started, "
            f"bot=@{credential.bot_username}"
        )

        try:
            while self.running:
                try:
                    updates = await client.get_updates(
                        offset=offset,
                        timeout=self.POLL_TIMEOUT_SECONDS,
                        allowed_updates=["message"],
                    )
                except TelegramSDKError as e:
                    if "Conflict" in e.code or "terminated by setWebhook" in e.code:
                        logger.warning(
                            f"[telegram:{credential.agent_id}] getUpdates 409 — "
                            f"webhook still set; calling deleteWebhook"
                        )
                        try:
                            await client.delete_webhook()
                        except TelegramSDKError:
                            pass
                        await asyncio.sleep(1.0)
                        continue
                    raise

                for update in updates:
                    if not self.running:
                        break
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = int(update_id) + 1
                        self._poll_offsets[key] = offset
                    yield update

                if not updates:
                    # No-op idle wake-up so self.running is checked
                    # periodically even when the bot is quiet.
                    await asyncio.sleep(self.POLL_IDLE_SLEEP_SECONDS)
        finally:
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._sdk_clients.pop(key, None)

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """Telegram update → ParsedMessage. None means "skip"."""
        msg = raw.get("message")
        if not msg:
            # We only allowed "message" updates; ignore everything else.
            return None
        text = msg.get("text", "") or ""
        if not text:
            # Phase 4 is text-only. Skip media/voice/file/sticker/etc.
            # (See reference/self_notebook/todo/2026-05-09-multimodal-im-ingest.md.)
            return None

        from_user = msg.get("from", {})
        sender_id = str(from_user.get("id", "") or "")
        if not sender_id:
            return None

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", "") or "")
        if not chat_id:
            return None

        chat_type_raw = chat.get("type", "private")
        chat_type = (
            ChatType.GROUP if chat_type_raw in ("group", "supergroup", "channel")
            else ChatType.PRIVATE
        )

        first = from_user.get("first_name", "") or ""
        last = from_user.get("last_name", "") or ""
        sender_name = " ".join(p for p in (first, last) if p) or sender_id

        # Mentions: parse "entities" of type "mention" / "text_mention"
        mentions: list[str] = []
        for ent in msg.get("entities", []):
            if not isinstance(ent, dict):
                continue
            etype = ent.get("type", "")
            offset = ent.get("offset", 0)
            length = ent.get("length", 0)
            if etype == "mention":
                # Plain @username — extract from text
                token = text[offset : offset + length].lstrip("@")
                if token:
                    mentions.append(token)
            elif etype == "text_mention":
                # Inline mention with user object
                user = ent.get("user", {}) or {}
                uid = user.get("id")
                if uid is not None:
                    mentions.append(str(uid))

        thread_id = msg.get("message_thread_id")
        thread_id_str: Optional[str] = str(thread_id) if thread_id is not None else None

        reply_msg = msg.get("reply_to_message") or {}
        reply_to_id = reply_msg.get("message_id")
        reply_to_str: Optional[str] = str(reply_to_id) if reply_to_id is not None else None

        return ParsedMessage(
            message_id=str(msg["message_id"]),
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=text,
            content_type=MessageContentType.TEXT,
            chat_type=chat_type,
            timestamp_ms=int(msg.get("date", 0)) * 1000,
            reply_to_message_id=reply_to_str,
            thread_id=thread_id_str,
            mentions=mentions,
            raw=raw,
        )

    async def is_echo(
        self, message: ParsedMessage, credential: TelegramCredential
    ) -> bool:
        """True when the message was sent by our own bot user."""
        if not credential.bot_user_id:
            return False
        return message.sender_id == credential.bot_user_id

    async def resolve_sender_name(
        self, sender_id: str, credential: TelegramCredential
    ) -> str:
        """Telegram messages already carry first/last name in the payload —
        ``parse_event`` extracts it into ``sender_name``. This resolver is
        the fallback used by the base class' inbox writer when the parsed
        name is empty (rare). We just return ``sender_id`` rather than
        burning an API call (Telegram has no general user-info-by-id API
        without a chat context)."""
        return sender_id

    def create_context_builder(
        self,
        message: ParsedMessage,
        credential: TelegramCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        return TelegramContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
        )

    # ────────────────────────────────────────────────────────────────────
    # Reply-side overrides
    # ────────────────────────────────────────────────────────────────────

    def extract_output(
        self, result, message: ParsedMessage, credential: TelegramCredential
    ) -> str:
        """Pull reply text from the AgentRuntime result for inbox display.

        Phase 3 Slack regression — DO NOT use ``result.output_text``;
        it contains the agent's reasoning ("My thought process: ...")
        and would leak chain-of-thought into the inbox. Instead scrape
        the ``tg_cli`` tool-call args for ``method=sendMessage``.
        """
        replies: list[str] = []
        for raw in getattr(result, "raw_items", []) or []:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if item.get("type") != "tool_call_item":
                continue
            sent_text = self._extract_tg_reply(item)
            if sent_text:
                replies.append(sent_text)

        if replies:
            output_text = "\n".join(replies)
        else:
            # Agent produced reasoning but never called tg_cli to send.
            output_text = "(stayed silent)"

        bot_label = credential.bot_username or credential.agent_id
        logger.info(
            f"TelegramTrigger [{bot_label}] agent responded: {output_text[:200]}"
        )
        return output_text

    @staticmethod
    def _extract_tg_reply(item: dict) -> str:
        """Extract sent text from a ``tg_cli`` tool call item.

        ``tg_cli`` signature: ``(agent_id, method, args)`` where ``args``
        is a dict. For inbox display we only care about
        ``method == "sendMessage"`` and ``args["text"]``.
        Other Telegram methods (sendChatAction, deleteMessage,
        editMessageText, etc.) are not user-visible reply text.
        """
        tool_name = item.get("tool_name", "")
        if "tg_cli" not in tool_name:
            return ""

        raw_args = item.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                return ""
        if not isinstance(raw_args, dict):
            return ""

        if raw_args.get("method") != "sendMessage":
            return ""

        inner = raw_args.get("args") or {}
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except (ValueError, TypeError):
                return ""
        if not isinstance(inner, dict):
            return ""

        text = inner.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def format_error_reply(self, error) -> str:
        """User-friendly error string for the inbox + Telegram reply."""
        return (
            f"Sorry, I hit an error processing your message: "
            f"{getattr(error, 'message', '') or 'unknown'}"
        )


# Convenience for `re` users — keep import explicit to avoid lint warning
_ = re  # currently unused at module scope, retained for future entity parsing
