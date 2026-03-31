"""
@file_name: telegram_trigger.py
@author: NarraNexus
@date: 2026-03-29
@description: TelegramTrigger — background message polling process

Single-process poller that monitors all Agent Telegram credentials for new messages.
Architecture: 1 Poller + N Workers.

Key design decisions:
- Uses Telegram Bot API long-polling (getUpdates) instead of webhooks.
  This avoids the need for a public HTTPS endpoint and simplifies deployment.
- Per-chat batching: multiple messages from the same chat are collapsed into ONE
  AgentRuntime call using the latest message as trigger. EventMemoryModule handles
  conversation history upstream, so the agent sees the full picture.
- Two-tier dedup (in-memory + DB): survives process restarts without re-processing.
- /start and /help commands are handled inline without invoking AgentRuntime.
- Group chats require @mention of the bot; DMs are always processed.
- LoggingService disabled in workers: the trigger process has its own file logger
  via service_logger. AgentRuntime workers share this logger instead of each creating
  their own log handler (prevents concurrent file operation race conditions).

Usage:
    uv run python -m xyz_agent_context.module.telegram_module.telegram_trigger
"""

from __future__ import annotations

import asyncio
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from loguru import logger

from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.inbox_schema import InboxMessageType
from xyz_agent_context.schema.runtime_message import MessageType
from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.utils import DatabaseClient, get_db_client

from ._telegram_credential_manager import TelegramCredentialManager, TelegramCredential
from ._telegram_dedup import TelegramUpdateDedup
from ._telegram_client import TelegramBotClient
from .telegram_context_builder import TelegramContextBuilder
from xyz_agent_context.channel.channel_context_builder_base import ChannelHistoryConfig


# === Polling constants ===
POLL_IDLE_CHECK_INTERVAL = 15    # Seconds between credential checks when no active bots
LONG_POLL_TIMEOUT = 30           # Telegram getUpdates timeout parameter

# === Safety net: prevent extreme agent-to-agent loops ===
ROOM_RATE_LIMIT_MAX = 20         # Max triggers per agent per chat in the window
ROOM_RATE_LIMIT_WINDOW = 1800    # Window in seconds (30 min)

# === Dedup cleanup ===
DEDUP_CLEANUP_INTERVAL = 3600    # Run cleanup every hour
DEDUP_RETENTION_DAYS = 7         # Keep processed updates for 7 days

# === Worker pool ===
DEFAULT_WORKERS = 3

# === Command responses ===
START_GREETING = (
    "Hello! I'm an AI agent powered by NarraNexus.\n\n"
    "You can send me a message and I'll respond. "
    "In group chats, mention me with @{bot_username} to get my attention."
)
HELP_TEXT = (
    "I'm a NarraNexus AI agent. Here's how to interact with me:\n\n"
    "- <b>Direct messages</b>: Just send me any message.\n"
    "- <b>Group chats</b>: Mention me with @{bot_username} in your message.\n"
    "- <b>/start</b>: Show the welcome message.\n"
    "- <b>/help</b>: Show this help text."
)


@dataclass
class ChatBatch:
    """Messages from one chat, collapsed into a single trigger."""
    chat_id: int
    chat_title: str
    chat_type: str  # "private", "group", "supergroup"
    latest_message: dict
    all_update_ids: list[int] = field(default_factory=list)


@dataclass
class AgentTask:
    """
    One unit of work for a worker: all pending chats for one agent.

    Replaces per-message tasks with per-agent batching.
    Now: 1 agent = 1 task, containing batched messages per chat.
    """
    credential: TelegramCredential
    chat_batches: list[ChatBatch] = field(default_factory=list)


class TelegramTrigger:
    """
    Background polling service for Telegram messages.

    Architecture: 1 Poller coroutine + N Worker coroutines.
    Polls all active Agent credentials for new messages via Telegram long-polling,
    then dispatches batched processing to the worker pool.

    Args:
        max_workers: Max concurrent message processing workers
        history_config: Conversation history loading config
    """

    def __init__(
        self,
        max_workers: int = DEFAULT_WORKERS,
        idle_check_interval: int = POLL_IDLE_CHECK_INTERVAL,
        history_config: Optional[ChannelHistoryConfig] = None,
    ):
        self.max_workers = max_workers
        self.idle_check_interval = idle_check_interval
        self.history_config = history_config or ChannelHistoryConfig()

        # Lazy-init resources
        self._db: Optional[DatabaseClient] = None
        self._cred_mgr: Optional[TelegramCredentialManager] = None
        self._dedup: Optional[TelegramUpdateDedup] = None

        # Runtime state
        self.running = False
        self._task_queue: asyncio.Queue[AgentTask] = asyncio.Queue()
        self._processing_agents: Set[str] = set()

        # Safety net: track (agent_id, chat_id) -> list of timestamps
        self._chat_rate_tracker: Dict[str, List[datetime]] = {}

        # Per-agent last processed update_id (for getUpdates offset)
        self._update_offsets: Dict[str, int] = {}

        # Coroutine handles
        self._workers: List[asyncio.Task] = []
        self._poller_task: Optional[asyncio.Task] = None
        self._last_dedup_cleanup: datetime = datetime.now(timezone.utc)

    # =========================================================================
    # Properties (lazy init)
    # =========================================================================

    @property
    def db(self) -> DatabaseClient:
        if self._db is None:
            raise RuntimeError("Database not initialized. Call start() first.")
        return self._db

    @property
    def cred_mgr(self) -> TelegramCredentialManager:
        if self._cred_mgr is None:
            self._cred_mgr = TelegramCredentialManager(self.db)
        return self._cred_mgr

    @property
    def dedup(self) -> TelegramUpdateDedup:
        if self._dedup is None:
            self._dedup = TelegramUpdateDedup(self.db)
        return self._dedup

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """
        Start the TelegramTrigger (Worker Pool mode).

        1 Poller + N Workers. Runs until stop() is called or interrupted.
        """
        if self._db is None:
            self._db = await get_db_client()
            logger.info("TelegramTrigger: database client initialized")

        # Ensure dedup table exists
        await self._ensure_credentials_table()
        await self._ensure_dedup_table()

        logger.info("=" * 60)
        logger.info("TelegramTrigger starting (Worker Pool mode)")
        logger.info(f"  Max workers: {self.max_workers}")
        logger.info(f"  Long-poll timeout: {LONG_POLL_TIMEOUT}s")
        logger.info(f"  Idle check interval: {self.idle_check_interval}s")
        logger.info(f"  History loading: {self.history_config.load_conversation_history}")
        logger.info("=" * 60)

        self.running = True

        # Launch worker coroutines
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)

        # Launch poller coroutine
        self._poller_task = asyncio.create_task(self._poller())

        try:
            await asyncio.gather(self._poller_task, *self._workers)
        except asyncio.CancelledError:
            logger.info("TelegramTrigger tasks cancelled")

        logger.info("TelegramTrigger stopped")

    async def _ensure_credentials_table(self) -> None:
        """Create credentials table if it doesn't exist (idempotent)."""
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS `telegram_credentials` (
                    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    `agent_id` VARCHAR(64) NOT NULL,
                    `bot_token` VARCHAR(256) NOT NULL,
                    `bot_username` VARCHAR(128) NULL,
                    `bot_id` BIGINT NULL,
                    `allowed_user_ids` JSON NULL,
                    `is_active` TINYINT(1) NOT NULL DEFAULT 1,
                    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uk_agent_id` (`agent_id`),
                    INDEX `idx_is_active` (`is_active`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                fetch=False,
            )
        except Exception as e:
            logger.warning(f"Failed to ensure credentials table: {e}")

    async def _ensure_dedup_table(self) -> None:
        """Create dedup table if it doesn't exist (idempotent)."""
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS `telegram_processed_updates` (
                    `update_id` BIGINT NOT NULL,
                    `agent_id` VARCHAR(64) NOT NULL,
                    `processed_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (`update_id`, `agent_id`),
                    INDEX `idx_processed_at` (`processed_at`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                fetch=False,
            )
        except Exception as e:
            logger.warning(f"Failed to ensure dedup table: {e}")

    async def stop(self) -> None:
        """Gracefully stop the trigger."""
        logger.info("Stopping TelegramTrigger gracefully...")
        self.running = False

        # Wait for queue to drain
        try:
            await asyncio.wait_for(self._task_queue.join(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for queue to drain, forcing shutdown")

        # Cancel all coroutines
        if self._poller_task:
            self._poller_task.cancel()
        for w in self._workers:
            w.cancel()

        tasks = [t for t in ([self._poller_task] + self._workers) if t is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._poller_task = None

    # =========================================================================
    # Helpers
    # =========================================================================

    def _check_chat_rate_limit(self, agent_id: str, chat_id: int) -> bool:
        """
        Check if agent has exceeded the safety-net rate limit for a chat.

        Returns True if the message should be SKIPPED (rate limited).
        High-threshold safety net -- normal conversations never hit this.
        """
        key = f"{agent_id}:{chat_id}"
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=ROOM_RATE_LIMIT_WINDOW)

        timestamps = self._chat_rate_tracker.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= ROOM_RATE_LIMIT_MAX:
            logger.warning(
                f"Safety net: agent {agent_id} hit rate limit in chat {chat_id} "
                f"({len(timestamps)} triggers in {ROOM_RATE_LIMIT_WINDOW}s). Skipping."
            )
            self._chat_rate_tracker[key] = timestamps
            return True

        timestamps.append(now)
        self._chat_rate_tracker[key] = timestamps
        return False

    @staticmethod
    def _check_mention(message: dict, bot_username: str) -> bool:
        """
        Check if the bot is mentioned in the message.

        Detection rules:
        1. Check message text for @bot_username substring
        2. Check message entities for type=="mention" matching @bot_username

        Args:
            message: Telegram Message dict
            bot_username: Bot's username (without @)

        Returns:
            True if the bot is mentioned
        """
        mention_text = f"@{bot_username}"
        text = message.get("text", "")

        # Simple substring check
        if mention_text.lower() in text.lower():
            return True

        # Entity-based check (more reliable for Telegram clients that use entities)
        entities = message.get("entities", [])
        for entity in entities:
            if entity.get("type") == "mention":
                offset = entity.get("offset", 0)
                length = entity.get("length", 0)
                entity_text = text[offset:offset + length]
                if entity_text.lower() == mention_text.lower():
                    return True

        return False

    @staticmethod
    def _is_command(message: dict, command: str) -> bool:
        """
        Check if a message is a specific bot command (e.g. /start, /help).

        Handles both "/command" and "/command@botname" formats.

        Args:
            message: Telegram Message dict
            command: Command to check (without slash, e.g. "start")

        Returns:
            True if message is the specified command
        """
        text = message.get("text", "").strip()
        if not text:
            return False

        # Exact match: "/start" or "/start@mybotname"
        if text == f"/{command}" or text.startswith(f"/{command}@"):
            return True

        # Check entities for bot_command type
        entities = message.get("entities", [])
        for entity in entities:
            if entity.get("type") == "bot_command":
                offset = entity.get("offset", 0)
                length = entity.get("length", 0)
                cmd_text = text[offset:offset + length]
                if cmd_text == f"/{command}" or cmd_text.startswith(f"/{command}@"):
                    return True

        return False

    # =========================================================================
    # Poller
    # =========================================================================

    async def _poller(self) -> None:
        """
        Poller coroutine: iterate over active credentials and poll for updates.

        Uses Telegram long-polling (getUpdates) per credential.
        """
        while self.running:
            try:
                active_creds = await self.cred_mgr.get_all_active()

                if not active_creds:
                    logger.debug("No active Telegram credentials, sleeping...")
                    await asyncio.sleep(self.idle_check_interval)
                    continue

                for cred in active_creds:
                    if not self.running:
                        break

                    # Skip agents currently being processed
                    if cred.agent_id in self._processing_agents:
                        logger.debug(
                            f"[{cred.agent_id}] Still processing, skipping poll"
                        )
                        continue

                    try:
                        await self._poll_credential(cred)
                    except Exception as e:
                        # Redact bot token from error messages to prevent leaking in logs
                        err_msg = str(e)
                        if cred.bot_token and cred.bot_token in err_msg:
                            err_msg = err_msg.replace(cred.bot_token, "***REDACTED***")
                        logger.error(
                            f"Error polling credential for {cred.agent_id} "
                            f"(@{cred.bot_username}): {err_msg}"
                        )

                # Periodic dedup cleanup
                now = datetime.now(timezone.utc)
                if (now - self._last_dedup_cleanup).total_seconds() > DEDUP_CLEANUP_INTERVAL:
                    try:
                        await self.dedup.cleanup_expired(DEDUP_RETENTION_DAYS)
                        self._last_dedup_cleanup = now
                        logger.debug("Dedup cleanup completed")
                    except Exception as e:
                        logger.warning(f"Dedup cleanup failed: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TelegramTrigger poller error: {e}")
                await asyncio.sleep(5)

    async def _poll_credential(self, cred: TelegramCredential) -> None:
        """
        Poll a single Telegram bot credential for new updates.

        Flow:
        1. Call getUpdates with stored offset
        2. Handle /start and /help commands inline
        3. Filter updates (DM vs group mention, allowlist, dedup)
        4. Batch remaining updates by chat_id
        5. Rate limit check
        6. Enqueue AgentTask if any batches remain
        7. Advance offset to prevent re-processing

        Args:
            cred: Active TelegramCredential to poll
        """
        client = TelegramBotClient(cred.bot_token)
        try:
            offset = self._update_offsets.get(cred.agent_id, 0)

            updates = await client.get_updates(
                offset=offset,
                timeout=LONG_POLL_TIMEOUT,
                allowed_updates=["message"],
            )

            if not updates:
                logger.debug(
                    f"[{cred.agent_id}] @{cred.bot_username}: no new updates"
                )
                return

            logger.debug(
                f"[{cred.agent_id}] @{cred.bot_username}: "
                f"received {len(updates)} update(s)"
            )

            # Track the maximum update_id to advance offset after processing
            max_update_id = max(u.get("update_id", 0) for u in updates)

            # === Phase 1: Filter and classify updates ===
            command_updates: List[dict] = []         # /start, /help — handled inline
            candidate_updates: List[dict] = []       # Potential triggers for AgentRuntime

            for update in updates:
                message = update.get("message")
                if not message:
                    continue

                update_id = update.get("update_id", 0)
                chat = message.get("chat", {})
                from_user = message.get("from", {})
                chat_type = chat.get("type", "private")
                sender_id = from_user.get("id", 0)

                # Skip messages from the bot itself
                if sender_id == cred.bot_id:
                    continue

                # Allowlist check: if configured, only accept listed users
                if cred.allowed_user_ids and sender_id not in cred.allowed_user_ids:
                    logger.debug(
                        f"[{cred.agent_id}] Allowlist skip: user {sender_id} "
                        f"not in {cred.allowed_user_ids}"
                    )
                    continue

                # Handle /start command
                if self._is_command(message, "start"):
                    command_updates.append(update)
                    continue

                # Handle /help command
                if self._is_command(message, "help"):
                    command_updates.append(update)
                    continue

                # Group chat: require @mention
                if chat_type in ("group", "supergroup"):
                    if not self._check_mention(message, cred.bot_username):
                        logger.debug(
                            f"[{cred.agent_id}] MENTION skip in chat {chat.get('id')}: "
                            f"no @{cred.bot_username} mention"
                        )
                        continue

                # DM (private) -> always process
                candidate_updates.append(update)

            # === Phase 2: Handle commands inline ===
            for update in command_updates:
                message = update.get("message", {})
                chat_id = message.get("chat", {}).get("id")
                if not chat_id:
                    continue

                try:
                    if self._is_command(message, "start"):
                        greeting = START_GREETING.format(bot_username=cred.bot_username)
                        await client.send_message(chat_id, greeting, parse_mode="")
                        logger.info(
                            f"[{cred.agent_id}] Sent /start greeting to chat {chat_id}"
                        )
                    elif self._is_command(message, "help"):
                        help_msg = HELP_TEXT.format(bot_username=cred.bot_username)
                        await client.send_message(chat_id, help_msg, parse_mode="HTML")
                        logger.info(
                            f"[{cred.agent_id}] Sent /help text to chat {chat_id}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[{cred.agent_id}] Failed to send command response "
                        f"to chat {chat_id}: {e}"
                    )

            # === Phase 3: Dedup check ===
            if not candidate_updates:
                # Advance offset even if all updates were commands/filtered
                self._update_offsets[cred.agent_id] = max_update_id + 1
                return

            candidate_update_ids = [u.get("update_id", 0) for u in candidate_updates]
            already_processed = await self.dedup.filter_processed(
                cred.agent_id, candidate_update_ids
            )
            # filter_processed returns IDs that are NOT yet processed
            new_update_ids = set(already_processed)

            passing_updates = [
                u for u in candidate_updates
                if u.get("update_id", 0) in new_update_ids
            ]

            if not passing_updates:
                logger.debug(
                    f"[{cred.agent_id}] All {len(candidate_updates)} candidate "
                    f"updates already processed"
                )
                self._update_offsets[cred.agent_id] = max_update_id + 1
                return

            # === Phase 4: Batch by chat_id ===
            chat_batches_map: Dict[int, ChatBatch] = {}

            for update in passing_updates:
                message = update["message"]
                update_id = update.get("update_id", 0)
                chat = message.get("chat", {})
                chat_id = chat.get("id", 0)
                chat_title = chat.get("title", "") or self._build_chat_name(message)
                chat_type = chat.get("type", "private")

                if chat_id not in chat_batches_map:
                    chat_batches_map[chat_id] = ChatBatch(
                        chat_id=chat_id,
                        chat_title=chat_title,
                        chat_type=chat_type,
                        latest_message=message,
                        all_update_ids=[update_id],
                    )
                else:
                    batch = chat_batches_map[chat_id]
                    batch.all_update_ids.append(update_id)
                    # Keep the latest message (highest message_id = newest)
                    if message.get("message_id", 0) > batch.latest_message.get("message_id", 0):
                        batch.latest_message = message

            # === Phase 5: Rate limit check per chat ===
            final_batches: List[ChatBatch] = []
            for batch in chat_batches_map.values():
                if self._check_chat_rate_limit(cred.agent_id, batch.chat_id):
                    logger.debug(
                        f"[{cred.agent_id}] RATE_LIMIT skip chat {batch.chat_id}"
                    )
                    # Still mark as processed to avoid re-triggering
                    await self.dedup.mark_processed(cred.agent_id, batch.all_update_ids)
                    continue
                final_batches.append(batch)

            # === Phase 6: Enqueue task ===
            if final_batches:
                task = AgentTask(
                    credential=cred,
                    chat_batches=final_batches,
                )
                await self._task_queue.put(task)

                total_updates = sum(len(b.all_update_ids) for b in final_batches)
                logger.info(
                    f"Enqueued {len(final_batches)} chat(s) / "
                    f"{total_updates} update(s) for agent {cred.agent_id} "
                    f"(@{cred.bot_username})"
                )

            # === Phase 7: Advance offset ===
            self._update_offsets[cred.agent_id] = max_update_id + 1

        finally:
            await client.close()

    @staticmethod
    def _build_chat_name(message: dict) -> str:
        """
        Build a display name for the chat from message sender info.

        Used for DMs where chat.title is empty.

        Args:
            message: Telegram Message dict

        Returns:
            Chat display name
        """
        from_user = message.get("from", {})
        return (
            from_user.get("first_name", "") + " " + from_user.get("last_name", "")
        ).strip() or f"Chat {message.get('chat', {}).get('id', 'unknown')}"

    # =========================================================================
    # Workers
    # =========================================================================

    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine: process agent tasks from the queue."""
        logger.info(f"[Worker {worker_id}] Started")
        while True:
            try:
                task = await self._task_queue.get()
                try:
                    self._processing_agents.add(task.credential.agent_id)
                    await self._process_task(task, worker_id)
                finally:
                    self._processing_agents.discard(task.credential.agent_id)
                    self._task_queue.task_done()
            except asyncio.CancelledError:
                logger.info(f"[Worker {worker_id}] Cancelled")
                break
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Unexpected error: {e}")

    async def _process_task(self, task: AgentTask, worker_id: int) -> None:
        """
        Process all chat batches for one agent.

        Each chat batch triggers one AgentRuntime call with the latest message.
        EventMemoryModule provides conversation history in the prompt.
        """
        cred = task.credential
        agent_id = cred.agent_id

        logger.info(
            f"[Worker {worker_id}] Processing task for {agent_id} "
            f"(@{cred.bot_username}): {len(task.chat_batches)} chat(s)"
        )

        for batch in task.chat_batches:
            try:
                await self._process_chat_batch(batch, cred, worker_id)
            except Exception as e:
                logger.error(
                    f"[Worker {worker_id}] Failed to process chat {batch.chat_id} "
                    f"for {agent_id}: {e}"
                )

    async def _process_chat_batch(
        self, batch: ChatBatch, cred: TelegramCredential, worker_id: int
    ) -> None:
        """
        Process a single chat batch through AgentRuntime.

        Flow:
        1. Send typing indicator
        2. Build prompt from latest message via TelegramContextBuilder
        3. Create ChannelTag with sender and chat info
        4. Call AgentRuntime.run() ONCE (not per-message)
        5. Send reply to Telegram
        6. Mark updates as processed in dedup
        7. Write result to Inbox
        """
        message = batch.latest_message
        agent_id = cred.agent_id
        from_user = message.get("from", {})
        sender_name = (
            from_user.get("first_name", "") + " " + from_user.get("last_name", "")
        ).strip() or "Unknown"
        sender_id = str(from_user.get("id", ""))
        body_preview = message.get("text", "")[:100]

        logger.info(
            f"[Worker {worker_id}] Processing {len(batch.all_update_ids)} update(s) "
            f"for {agent_id} in chat {batch.chat_id} — "
            f"from: {sender_name}, trigger: {body_preview}..."
        )

        client = TelegramBotClient(cred.bot_token)
        try:
            # 1. Send typing indicator
            try:
                await client.send_chat_action(str(batch.chat_id), "typing")
            except Exception as e:
                logger.debug(f"Failed to send typing indicator: {e}")

            # 2. Build prompt via TelegramContextBuilder
            builder = TelegramContextBuilder(
                message=message,
                credential=cred,
                client=client,
                agent_id=agent_id,
            )
            prompt = await builder.build_prompt(self.history_config)

            # 3. Create ChannelTag
            channel_tag = ChannelTag.telegram(
                sender_name=sender_name,
                sender_id=sender_id,
                chat_id=str(batch.chat_id),
                chat_title=batch.chat_title,
            )
            tagged_prompt = f"{channel_tag.format()}\n{prompt}"

            # 4. Call AgentRuntime with logging DISABLED
            #    (trigger process already has its own file logger via service_logger;
            #     per-worker LoggingService causes file race conditions)
            from xyz_agent_context.agent_runtime import AgentRuntime
            from xyz_agent_context.agent_runtime.logging_service import LoggingService

            runtime = AgentRuntime(logging_service=LoggingService(enabled=False))
            user_visible_parts: List[str] = []

            # Keep sending "typing..." every 4s until AgentRuntime finishes
            typing_task = asyncio.create_task(
                self._keep_typing(client, str(batch.chat_id))
            )

            try:
                async for response in runtime.run(
                    agent_id=agent_id,
                    user_id=sender_id,
                    input_content=tagged_prompt,
                    working_source=WorkingSource.TELEGRAM,
                    trigger_extra_data={
                        "channel_tag": channel_tag.to_dict(),
                        "telegram_chat_id": batch.chat_id,
                    },
                ):
                    # Extract user-visible content from send_message_to_user_directly tool calls
                    # (NOT from AgentTextDelta which contains the agent's thinking process)
                    if hasattr(response, "message_type"):
                        if response.message_type == MessageType.PROGRESS:
                            details = getattr(response, "details", None)
                            if details and isinstance(details, dict):
                                tool_name = details.get("tool_name", "")
                                if tool_name.endswith("send_message_to_user_directly"):
                                    arguments = details.get("arguments", {})
                                    content_part = arguments.get("content", "")
                                    if content_part:
                                        user_visible_parts.append(content_part)
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

            content = "\n\n".join(user_visible_parts)

            # 5. NO auto-reply here — the agent sends replies via telegram_send_message
            #    or telegram_reply_to_message MCP tools during its reasoning loop.
            #    This matches MatrixTrigger which also does NOT auto-reply to rooms.
            if content:
                logger.info(
                    f"[Worker {worker_id}] Agent responded in chat {batch.chat_id} "
                    f"({len(content)} chars via MCP tools)"
                )
            else:
                logger.info(
                    f"[Worker {worker_id}] Agent produced no user-visible output "
                    f"for chat {batch.chat_id}"
                )

            # 6. Mark updates as processed in dedup
            await self.dedup.mark_processed(cred.agent_id, batch.all_update_ids)

            # 7. Write to Inbox
            await self._write_to_inbox(cred, message, content, chat_id=batch.chat_id)

            logger.info(
                f"[Worker {worker_id}] Batch processed for {agent_id} "
                f"chat={batch.chat_id}, output length: {len(content)}"
            )

        finally:
            await client.close()

    async def _keep_typing(self, client: TelegramBotClient, chat_id: str) -> None:
        """
        Send typing indicator every 4 seconds until cancelled.

        Telegram's typing status expires after ~5 seconds, so we resend
        periodically to keep it visible while the agent is processing.
        """
        try:
            while True:
                await client.send_chat_action(chat_id, "typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.debug(f"Typing indicator stopped: {e}")

    async def _write_to_inbox(
        self,
        cred: TelegramCredential,
        message: dict,
        agent_output: str,
        chat_id: int = 0,
    ) -> None:
        """
        Write Telegram conversation result to the Agent owner's Inbox.

        Follows the same pattern as MatrixTrigger._write_to_inbox:
        looks up the agent's owner user_id and writes a structured
        inbox record.
        """
        import json as _json

        try:
            from_user = message.get("from", {})
            sender_name = (
                from_user.get("first_name", "") + " " + from_user.get("last_name", "")
            ).strip() or "Unknown"
            sender_id = str(from_user.get("id", ""))
            chat = message.get("chat", {})
            chat_title = chat.get("title", "") or sender_name
            actual_chat_id = chat_id or chat.get("id", 0)

            title = f"Telegram: {sender_name} in {chat_title}"
            content = (
                f"**From**: {sender_name}\n"
                f"**Chat**: {chat_title}\n"
                f"**Message**: {message.get('text', '')}\n\n"
                f"---\n\n"
                f"**Your response**:\n{agent_output}"
            )

            msg_id = f"msg_{uuid4().hex[:16]}"

            # Look up agent owner
            from xyz_agent_context.repository import AgentRepository
            agent_repo = AgentRepository(self.db)
            try:
                agent = await agent_repo.get_by_id(cred.agent_id)
                owner_user_id = agent.created_by if agent else ""
            except Exception as e:
                logger.warning(f"Failed to look up agent owner for {cred.agent_id}: {e}")
                owner_user_id = ""

            if not owner_user_id:
                logger.warning(
                    f"Cannot write to inbox: no owner found for agent {cred.agent_id}"
                )
                return

            source_json = _json.dumps({
                "type": "telegram",
                "id": cred.agent_id,
                "chat_id": actual_chat_id,
                "chat_title": chat_title,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "bot_username": cred.bot_username,
            }, ensure_ascii=False)

            from xyz_agent_context.utils import utc_now
            await self.db.insert("inbox_table", {
                "message_id": msg_id,
                "user_id": owner_user_id,
                "source": source_json,
                "event_id": str(message.get("message_id", "")) or None,
                "message_type": InboxMessageType.CHANNEL_MESSAGE.value,
                "title": title,
                "content": content,
                "is_read": False,
                "created_at": utc_now(),
            })

            logger.debug(
                f"Wrote inbox message {msg_id} for agent {cred.agent_id} "
                f"owner={owner_user_id}"
            )

        except Exception as e:
            logger.error(f"Failed to write Telegram message to inbox: {e}")


# =============================================================================
# Entry Points
# =============================================================================

def run_telegram_trigger(
    max_workers: int = DEFAULT_WORKERS,
) -> None:
    """
    Run TelegramTrigger (called by ModuleRunner or standalone).
    """
    import xyz_agent_context.settings  # noqa: F401

    trigger = TelegramTrigger(max_workers=max_workers)
    asyncio.run(trigger.start())


def main():
    """CLI entry point for TelegramTrigger."""
    parser = argparse.ArgumentParser(
        description="TelegramTrigger — Background Telegram Message Poller",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=DEFAULT_WORKERS,
        help=f"Max concurrent workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--interval", "-i", type=int, default=POLL_IDLE_CHECK_INTERVAL,
        help=f"Idle check interval in seconds (default: {POLL_IDLE_CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--no-history", action="store_true",
        help="Disable conversation history loading in prompts",
    )
    args = parser.parse_args()

    from xyz_agent_context.utils.service_logger import setup_service_logger
    setup_service_logger("telegram_trigger")

    logger.info("=" * 60)
    logger.info("TelegramTrigger — Background Telegram Message Poller")
    logger.info(f"  Max workers: {args.workers}")
    logger.info(f"  Idle check interval: {args.interval}s")
    logger.info(f"  History: {'disabled' if args.no_history else 'enabled'}")
    logger.info("=" * 60)
    logger.info("Press Ctrl+C to stop")

    history_config = ChannelHistoryConfig(
        load_conversation_history=not args.no_history,
    )

    trigger = TelegramTrigger(
        max_workers=args.workers,
        idle_check_interval=args.interval,
        history_config=history_config,
    )

    import xyz_agent_context.settings  # noqa: F401

    try:
        asyncio.run(trigger.start())
    except KeyboardInterrupt:
        logger.info("TelegramTrigger shutting down")


if __name__ == "__main__":
    main()
