"""
@file_name: inbox.py
@author: NexusAgent
@date: 2026-04-09
@description: Agent Inbox API — exposes MessageBus channels and messages to the frontend

Endpoints:
  GET  /api/agent-inbox              — list channels with messages for an agent
  PUT  /api/agent-inbox/{message_id}/read — mark a message as read
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from loguru import logger

router = APIRouter()


async def _get_db():
    from xyz_agent_context.utils.db_factory import get_db_client
    return await get_db_client()


async def _resolve_agent_names(db, agent_ids: list[str]) -> dict[str, str]:
    """Resolve agent_id -> agent_name. Returns dict mapping id to name."""
    if not agent_ids:
        return {}
    rows = await db.get_by_ids("agents", "agent_id", agent_ids)
    return {
        r["agent_id"]: r.get("agent_name", r["agent_id"])
        for r in rows if r
    }


@router.get("")
async def get_agent_inbox(
    agent_id: str = Query(..., description="Agent ID"),
    is_read: Optional[bool] = Query(None, description="Filter by read status"),
    limit: Optional[int] = Query(None, description="Max messages per channel (-1 for unlimited)"),
):
    """
    Get all channels and messages for an agent.

    Returns data shaped for the frontend MatrixRoom-compatible format:
    {
      rooms: [{ room_id, room_name, members, unread_count, messages, latest_at }],
      total_unread: int
    }
    """
    try:
        db = await _get_db()

        # 1. Get all channels this agent is a member of
        member_rows = await db.get("bus_channel_members", {"agent_id": agent_id})
        if not member_rows:
            return {"success": True, "rooms": [], "total_unread": 0}

        channel_ids = [r["channel_id"] for r in member_rows]
        # Build cursor map: channel_id -> last_processed_at
        cursor_map = {
            r["channel_id"]: r.get("last_processed_at") or r.get("last_read_at") or "1970-01-01"
            for r in member_rows
        }

        # 2. Get channel details
        channel_rows = await db.get_by_ids("bus_channels", "channel_id", channel_ids)
        channel_map = {r["channel_id"]: r for r in channel_rows if r}

        # 3. Get all members for these channels
        all_members = []
        for cid in channel_ids:
            rows = await db.get("bus_channel_members", {"channel_id": cid})
            all_members.extend(rows)

        # Collect all unique agent_ids for name resolution
        all_agent_ids = list(set(
            [r["agent_id"] for r in all_members] + [agent_id]
        ))
        name_map = await _resolve_agent_names(db, all_agent_ids)

        # 4. Get messages per channel
        effective_limit = 50
        if limit is not None:
            effective_limit = 9999 if limit < 0 else limit

        total_unread = 0
        rooms = []

        for cid in channel_ids:
            channel = channel_map.get(cid)
            if not channel:
                continue

            cursor = cursor_map.get(cid, "1970-01-01")

            # Fetch messages — use %s (MySQL style); auto-translated for SQLite
            query = (
                f"SELECT * FROM bus_messages WHERE channel_id = %s "
                f"ORDER BY created_at DESC LIMIT {int(effective_limit)}"
            )
            msg_rows = await db.execute(query, (cid,))
            # Reverse to chronological order
            msg_rows = list(reversed(msg_rows))

            # Count unread (messages after cursor, not from self)
            unread = sum(
                1 for m in msg_rows
                if m.get("from_agent") != agent_id
                and (m.get("created_at", "") > cursor)
            )
            total_unread += unread

            # Filter by is_read if specified
            if is_read is not None:
                if is_read:
                    msg_rows = [
                        m for m in msg_rows
                        if m.get("created_at", "") <= cursor or m.get("from_agent") == agent_id
                    ]
                else:
                    msg_rows = [
                        m for m in msg_rows
                        if m.get("from_agent") != agent_id and m.get("created_at", "") > cursor
                    ]

            # Build members list for this channel
            channel_members = [r for r in all_members if r["channel_id"] == cid]
            members = [
                {
                    "agent_id": m["agent_id"],
                    "agent_name": name_map.get(m["agent_id"], m["agent_id"]),
                    "matrix_user_id": m["agent_id"],  # compat field
                }
                for m in channel_members
            ]

            # Build messages
            messages = []
            for m in msg_rows:
                sender = m.get("from_agent", "")
                msg_time = m.get("created_at", "")
                is_msg_read = (
                    sender == agent_id
                    or msg_time <= cursor
                )
                messages.append({
                    "message_id": m.get("message_id", ""),
                    "sender_id": sender,
                    "sender_name": name_map.get(sender, sender),
                    "content": m.get("content", ""),
                    "is_read": is_msg_read,
                    "created_at": msg_time,
                })

            latest_at = msg_rows[-1].get("created_at") if msg_rows else None

            rooms.append({
                "room_id": cid,
                "room_name": channel.get("name", cid),
                "members": members,
                "unread_count": unread,
                "messages": messages,
                "latest_at": latest_at,
            })

        # Sort rooms: unread first, then by latest message time desc
        rooms.sort(key=lambda r: (r["unread_count"] == 0, r.get("latest_at") or ""), reverse=True)

        return {
            "success": True,
            "rooms": rooms,
            "total_unread": total_unread,
        }

    except Exception as e:
        logger.error(f"[get_agent_inbox] Error: {e}", exc_info=True)
        return {"success": False, "rooms": [], "total_unread": 0, "error": str(e)}


@router.put("/{message_id}/read")
async def mark_message_read(message_id: str, agent_id: str = Query(...)):
    """
    Mark a message as read by advancing the read cursor.

    Finds the message, then updates the agent's last_read_at cursor
    for that channel to the message's timestamp.
    """
    try:
        db = await _get_db()

        # Find the message to get its channel and timestamp
        msg = await db.get_one("bus_messages", {"message_id": message_id})
        if not msg:
            return {"success": False, "error": "Message not found", "marked_count": 0}

        channel_id = msg["channel_id"]
        msg_time = msg.get("created_at", "")

        # Update the cursor — use %s (MySQL style); auto-translated for SQLite
        await db.execute(
            "UPDATE bus_channel_members SET last_read_at = %s "
            "WHERE channel_id = %s AND agent_id = %s AND (last_read_at IS NULL OR last_read_at < %s)",
            (msg_time, channel_id, agent_id, msg_time),
            fetch=False,
        )

        return {"success": True, "marked_count": 1}

    except Exception as e:
        logger.error(f"[mark_message_read] Error: {e}", exc_info=True)
        return {"success": False, "error": str(e), "marked_count": 0}
