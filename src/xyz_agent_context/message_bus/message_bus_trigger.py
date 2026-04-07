"""
@file_name: message_bus_trigger.py
@author: NarraNexus
@date: 2026-04-03
@description: Background poller that delivers pending messages to agents

Replaces MatrixTrigger. Polls bus_messages table, triggers AgentRuntime
for agents with unprocessed messages.

Design:
- Single poller cycles through all registered agents (from bus_agent_registry)
- Groups pending messages by channel_id (per-channel batching)
- For each channel with pending messages, triggers AgentRuntime.run()
- On success: advances the cursor via ack_processed()
- On failure: records failure via record_failure()

Usage:
    DATABASE_URL=sqlite:///path/to/db uv run python -m xyz_agent_context.message_bus.message_bus_trigger
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import Dict, List, Optional

from loguru import logger

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.schemas import BusMessage

# Poll interval in seconds
POLL_INTERVAL = 10

# Maximum concurrent agent processing workers
MAX_WORKERS = 3


class MessageBusTrigger:
    """
    Background poller that processes pending MessageBus messages.

    Cycles through all registered agents, finds unprocessed messages,
    and triggers AgentRuntime to handle them.

    Args:
        bus: A MessageBusService instance (typically LocalMessageBus).
        poll_interval: Seconds between poll cycles.
        max_workers: Maximum concurrent agent processing tasks.
    """

    def __init__(
        self,
        bus: LocalMessageBus,
        poll_interval: int = POLL_INTERVAL,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self._bus = bus
        self._poll_interval = poll_interval
        self._max_workers = max_workers
        self._running = False
        self._semaphore = asyncio.Semaphore(max_workers)

    async def start(self) -> None:
        """Start the polling loop."""
        self._running = True
        logger.info(
            f"MessageBusTrigger started (poll_interval={self._poll_interval}s, "
            f"max_workers={self._max_workers})"
        )

        while self._running:
            try:
                await self._poll_cycle()
            except Exception as e:
                logger.error(f"MessageBusTrigger poll cycle error: {e}")

            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False
        logger.info("MessageBusTrigger stopping")

    async def _poll_cycle(self) -> None:
        """
        Single poll cycle: fetch all registered agents and process
        pending messages for each.
        """
        # Get all registered agents
        agents = await self._bus.search_agents(query="", limit=1000)
        if not agents:
            return

        tasks = []
        for agent_info in agents:
            tasks.append(self._process_agent(agent_info.agent_id))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_agent(self, agent_id: str) -> None:
        """
        Process pending messages for a single agent.

        Groups messages by channel_id and triggers AgentRuntime for each
        channel batch (only the latest message triggers execution).
        """
        async with self._semaphore:
            try:
                pending = await self._bus.get_pending_messages(agent_id)
                if not pending:
                    return

                # Group by channel_id
                by_channel: Dict[str, List[BusMessage]] = defaultdict(list)
                for msg in pending:
                    by_channel[msg.channel_id].append(msg)

                for channel_id, messages in by_channel.items():
                    # Use the latest message as the trigger
                    latest = messages[-1]
                    await self._handle_channel_batch(
                        agent_id=agent_id,
                        channel_id=channel_id,
                        messages=messages,
                        trigger_message=latest,
                    )
            except Exception as e:
                logger.error(
                    f"MessageBusTrigger failed processing agent {agent_id}: {e}"
                )

    async def _handle_channel_batch(
        self,
        agent_id: str,
        channel_id: str,
        messages: List[BusMessage],
        trigger_message: BusMessage,
    ) -> None:
        """
        Handle a batch of messages from a single channel for an agent.

        Builds a prompt, invokes AgentRuntime, and on success advances the
        processing cursor. On failure, records the failure for retry tracking.
        """
        try:
            # Build prompt from messages
            prompt = self._build_prompt(messages)

            # Try to get channel name
            channel_name = channel_id  # fallback

            logger.info(
                f"MessageBusTrigger: triggering agent {agent_id} "
                f"for channel {channel_id} ({len(messages)} messages)"
            )

            # Call AgentRuntime
            response_text = await self._invoke_runtime(
                agent_id=agent_id,
                sender_agent_id=trigger_message.from_agent,
                prompt=prompt,
                channel_id=channel_id,
            )

            # On success: advance cursor
            await self._bus.ack_processed(
                agent_id=agent_id,
                channel_id=channel_id,
                up_to_timestamp=trigger_message.created_at,
            )

            logger.info(
                f"MessageBusTrigger: agent {agent_id} processed "
                f"{len(messages)} messages in channel {channel_id}"
            )

        except Exception as e:
            logger.error(
                f"MessageBusTrigger: failed to process channel {channel_id} "
                f"for agent {agent_id}: {e}"
            )
            # Record failure for the trigger message
            await self._bus.record_failure(
                message_id=trigger_message.message_id,
                agent_id=agent_id,
                error=str(e),
            )

    def _build_prompt(self, messages: List[BusMessage]) -> str:
        """
        Build a prompt from a list of pending messages.

        Includes all messages in the batch so the agent has full context.
        """
        lines = ["[Message Bus - Incoming Messages]", ""]
        for msg in messages:
            lines.append(
                f"From: {msg.from_agent}\n"
                f"Time: {msg.created_at}\n"
                f"{msg.content}\n"
            )
        return "\n".join(lines)

    async def _invoke_runtime(
        self,
        agent_id: str,
        sender_agent_id: str,
        prompt: str,
        channel_id: str,
    ) -> str:
        """
        Invoke AgentRuntime.run() for the given agent with the prompt.

        Returns the collected agent response text.

        Raises:
            RuntimeError: If AgentRuntime cannot be imported or execution fails.
        """
        try:
            from xyz_agent_context.agent_runtime import AgentRuntime
            from xyz_agent_context.agent_runtime.logging_service import LoggingService
            from xyz_agent_context.schema import MessageType, WorkingSource
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import AgentRuntime dependencies: {e}"
            ) from e

        runtime = AgentRuntime(logging_service=LoggingService(enabled=False))
        final_output: list[str] = []

        async for response in runtime.run(
            agent_id=agent_id,
            user_id=sender_agent_id,
            input_content=prompt,
            working_source=WorkingSource.MESSAGE_BUS,
            trigger_extra_data={"bus_channel_id": channel_id},
        ):
            if hasattr(response, "message_type"):
                if response.message_type == MessageType.AGENT_RESPONSE:
                    if hasattr(response, "delta") and response.delta:
                        final_output.append(response.delta)

        return "".join(final_output)


async def _get_bus() -> LocalMessageBus:
    """Create and return a LocalMessageBus instance from environment config."""
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
        create_bus_tables_sqlite,
    )

    # Extract path from sqlite:///path/to/db
    if db_url.startswith("sqlite:///"):
        db_path = db_url[len("sqlite:///"):]
    else:
        db_path = db_url

    backend = SQLiteBackend(db_path)
    await backend.initialize()

    # Ensure all tables exist (runs as separate process)
    from xyz_agent_context.utils.schema_registry import auto_migrate
    await auto_migrate(backend)

    # Ensure bus-specific tables exist
    await create_bus_tables_sqlite(backend)

    return LocalMessageBus(backend=backend)


async def main() -> None:
    """Entry point for standalone execution."""
    logger.info("Starting MessageBusTrigger...")
    bus = await _get_bus()
    trigger = MessageBusTrigger(bus=bus)

    try:
        await trigger.start()
    except KeyboardInterrupt:
        trigger.stop()
        logger.info("MessageBusTrigger stopped by user")


if __name__ == "__main__":
    asyncio.run(main())
