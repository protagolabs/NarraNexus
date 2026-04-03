"""
@file_name: local_bus.py
@author: NarraNexus
@date: 2026-04-02
@description: Local SQLite-backed implementation of the MessageBus service

Implements MessageBusService using a DatabaseBackend (typically SQLiteBackend).
Designed for single-node / desktop use. All state lives in the local database.

Key design decisions:
- Cursor-based delivery model via last_processed_at per channel member
- Poison message filtering: messages with >= 3 failures are skipped
- Agent capabilities stored as JSON-serialized list in the registry
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from xyz_agent_context.message_bus.message_bus_service import MessageBusService
from xyz_agent_context.message_bus.schemas import BusAgentInfo, BusMessage
from xyz_agent_context.utils.db_backend import DatabaseBackend


def _generate_id(prefix: str) -> str:
    """Generate a short random ID with the given prefix."""
    return f"{prefix}_{secrets.token_hex(4)}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class LocalMessageBus(MessageBusService):
    """
    SQLite-backed MessageBus implementation.

    Uses a DatabaseBackend instance for all persistence. Suitable for
    local/desktop deployments where all agents run on the same machine.

    Args:
        backend: An initialized DatabaseBackend (e.g., SQLiteBackend).
    """

    def __init__(self, backend: DatabaseBackend) -> None:
        self._db = backend

    # ===== Messaging =====

    async def send_message(
        self,
        from_agent: str,
        to_channel: str,
        content: str,
        msg_type: str = "text",
    ) -> str:
        """Send a message to a channel and return the generated message_id."""
        msg_id = _generate_id("msg")
        await self._db.insert("bus_messages", {
            "message_id": msg_id,
            "channel_id": to_channel,
            "from_agent": from_agent,
            "content": content,
            "msg_type": msg_type,
            "created_at": _now_iso(),
        })
        return msg_id

    async def get_messages(
        self,
        channel_id: str,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> List[BusMessage]:
        """Get messages from a channel, optionally filtered by timestamp."""
        ph = self._db.placeholder
        if since:
            rows = await self._db.execute(
                f'SELECT * FROM "bus_messages" WHERE "channel_id" = {ph} '
                f'AND "created_at" > {ph} ORDER BY "created_at" ASC LIMIT {int(limit)}',
                (channel_id, since),
            )
        else:
            rows = await self._db.execute(
                f'SELECT * FROM "bus_messages" WHERE "channel_id" = {ph} '
                f'ORDER BY "created_at" ASC LIMIT {int(limit)}',
                (channel_id,),
            )
        return [BusMessage(**row) for row in rows]

    async def get_unread(self, agent_id: str) -> List[BusMessage]:
        """Get all unread messages for an agent across all channels."""
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT m.* FROM bus_messages m "
            f"JOIN bus_channel_members cm ON m.channel_id = cm.channel_id "
            f"WHERE cm.agent_id = {ph} "
            f"AND m.created_at > COALESCE(cm.last_read_at, '1970-01-01') "
            f"ORDER BY m.created_at ASC",
            (agent_id,),
        )
        return [BusMessage(**row) for row in rows]

    async def mark_read(self, agent_id: str, message_ids: List[str]) -> None:
        """Mark messages as read by advancing the read cursor per channel."""
        if not message_ids:
            return

        # Fetch the messages to find their channel_id and created_at
        messages = await self._db.get_by_ids("bus_messages", "message_id", message_ids)

        # Group by channel_id and find the latest created_at per channel
        channel_latest: dict[str, str] = {}
        for msg in messages:
            if msg is None:
                continue
            ch = msg["channel_id"]
            ts = msg["created_at"]
            if ch not in channel_latest or ts > channel_latest[ch]:
                channel_latest[ch] = ts

        # Update last_read_at for each channel
        for ch_id, latest_ts in channel_latest.items():
            await self._db.update(
                "bus_channel_members",
                {"agent_id": agent_id, "channel_id": ch_id},
                {"last_read_at": latest_ts},
            )

    # ===== Channel Management =====

    async def create_channel(
        self,
        name: str,
        members: List[str],
        channel_type: str = "group",
    ) -> str:
        """Create a new channel with the given members."""
        ch_id = _generate_id("ch")
        now = _now_iso()
        created_by = members[0] if members else "system"

        await self._db.insert("bus_channels", {
            "channel_id": ch_id,
            "name": name,
            "channel_type": channel_type,
            "created_by": created_by,
            "created_at": now,
        })

        for agent_id in members:
            await self._db.insert("bus_channel_members", {
                "channel_id": ch_id,
                "agent_id": agent_id,
                "joined_at": now,
                "last_read_at": now,
            })

        return ch_id

    async def join_channel(self, agent_id: str, channel_id: str) -> None:
        """Add an agent to a channel."""
        now = _now_iso()
        await self._db.insert("bus_channel_members", {
            "channel_id": channel_id,
            "agent_id": agent_id,
            "joined_at": now,
            "last_read_at": now,
        })

    async def leave_channel(self, agent_id: str, channel_id: str) -> None:
        """Remove an agent from a channel."""
        await self._db.delete("bus_channel_members", {
            "channel_id": channel_id,
            "agent_id": agent_id,
        })

    # ===== Agent Discovery =====

    async def register_agent(
        self,
        agent_id: str,
        owner_user_id: str,
        capabilities: List[str],
        description: str,
        visibility: str = "private",
    ) -> None:
        """Register or update an agent in the discovery registry."""
        now = _now_iso()
        await self._db.upsert(
            "bus_agent_registry",
            {
                "agent_id": agent_id,
                "owner_user_id": owner_user_id,
                "capabilities": json.dumps(capabilities),
                "description": description,
                "visibility": visibility,
                "registered_at": now,
                "last_seen_at": now,
            },
            id_field="agent_id",
        )

    async def search_agents(
        self,
        query: str,
        limit: int = 10,
    ) -> List[BusAgentInfo]:
        """Search for agents by capability or description."""
        ph = self._db.placeholder
        search_pattern = f"%{query}%"
        rows = await self._db.execute(
            f"SELECT * FROM bus_agent_registry "
            f"WHERE capabilities LIKE {ph} OR description LIKE {ph} "
            f"LIMIT {int(limit)}",
            (search_pattern, search_pattern),
        )
        results = []
        for row in rows:
            caps = row.get("capabilities", "[]")
            if isinstance(caps, str):
                caps = json.loads(caps)
            results.append(BusAgentInfo(
                agent_id=row["agent_id"],
                owner_user_id=row["owner_user_id"],
                capabilities=caps,
                description=row.get("description", ""),
                visibility=row.get("visibility", "private"),
                registered_at=row.get("registered_at", ""),
                last_seen_at=row.get("last_seen_at", ""),
            ))
        return results

    # ===== Delivery =====

    async def get_pending_messages(
        self,
        agent_id: str,
        limit: int = 50,
    ) -> List[BusMessage]:
        """
        Get messages that have not been processed by the agent.

        Uses the cursor model and filters out self-sent messages
        and poison messages (failure_count >= 3).
        """
        ph = self._db.placeholder
        rows = await self._db.execute(
            f"SELECT m.* FROM bus_messages m "
            f"JOIN bus_channel_members cm ON m.channel_id = cm.channel_id "
            f"WHERE cm.agent_id = {ph} "
            f"AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01') "
            f"AND m.from_agent != {ph} "
            f"ORDER BY m.created_at ASC "
            f"LIMIT {int(limit)}",
            (agent_id, agent_id),
        )

        # Filter out poison messages (failure_count >= 3)
        result = []
        for row in rows:
            failure_count = await self.get_failure_count(row["message_id"], agent_id)
            if failure_count < 3:
                result.append(BusMessage(**row))
        return result

    async def ack_processed(
        self,
        agent_id: str,
        channel_id: str,
        up_to_timestamp: str,
    ) -> None:
        """Acknowledge messages up to a timestamp as processed."""
        await self._db.update(
            "bus_channel_members",
            {"agent_id": agent_id, "channel_id": channel_id},
            {"last_processed_at": up_to_timestamp},
        )

    async def record_failure(
        self,
        message_id: str,
        agent_id: str,
        error: str,
    ) -> None:
        """Record a delivery failure, incrementing retry_count."""
        now = _now_iso()
        existing = await self._db.get_one("bus_message_failures", {
            "message_id": message_id,
            "agent_id": agent_id,
        })
        if existing:
            await self._db.update(
                "bus_message_failures",
                {"message_id": message_id, "agent_id": agent_id},
                {
                    "retry_count": existing["retry_count"] + 1,
                    "last_error": error,
                    "last_retry_at": now,
                },
            )
        else:
            await self._db.insert("bus_message_failures", {
                "message_id": message_id,
                "agent_id": agent_id,
                "retry_count": 1,
                "last_error": error,
                "last_retry_at": now,
            })

    async def get_failure_count(
        self,
        message_id: str,
        agent_id: str,
    ) -> int:
        """Get the number of delivery failures for a message/agent pair."""
        row = await self._db.get_one("bus_message_failures", {
            "message_id": message_id,
            "agent_id": agent_id,
        })
        if row is None:
            return 0
        return row["retry_count"]
