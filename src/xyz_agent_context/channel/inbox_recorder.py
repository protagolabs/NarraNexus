"""
@file_name: inbox_recorder.py
@author:
@date: 2026-08-17
@description: Records a conversation turn into the inbox — the platform's own
record of conversations the user was not in.

Replaces ``ChannelInboxWriter``, which wrote a five-row bundle into the
MessageBus tables (pseudo-agent, channel, membership, inbound, reply). Two
things that cost, both measured on prod 2026-08-17:

* 86% of ``bus_messages`` (28,605 of 33,164 rows) was IM inbox content. The
  bus's own table was mostly another feature's storage.
* The membership row it created had a ``last_read_at`` nothing ever advanced —
  159 of 172 IM memberships (92%) sat at NULL. The bus unread predicate is
  ``created_at > COALESCE(last_read_at, epoch)``, so 1,364 IM messages were
  permanently "unread" and rode into 90 agents' turn context, attributed to
  pseudo-agents like ``lark_user_<id>`` that no Source-Recognition rule
  describes.

The rows now live in ``inbox_threads`` / ``inbox_thread_messages``, and the
agent's unread injection reads ``bus_messages JOIN bus_channel_members`` — so
the containment is structural. Not a prefix filter: a filter is what
``im_channel_prefixes()`` was, and it drifted (2026-07-03 wechat double
dispatch, three channels missing from a hand-maintained tuple).

WHAT THIS IS NOT: the notification store. ``InboxRepository`` / ``inbox_table``
is a different feature that shares the word — system alerts the platform pushes
at an owner. Nothing here touches it.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Optional, Sequence

from loguru import logger

from xyz_agent_context.utils import utc_now

#: Thread-id family prefixes. The family comes first so the namespace says WHAT
#: a row is before it says which one — and so any residual filter is trivially
#: correct rather than a list of channel names that can go stale.
IM_THREAD_PREFIX = "im_"
AGENT_DM_THREAD_PREFIX = "nx_dm_"

#: Direction of a row within a turn.
INBOUND = "in"
OUTBOUND = "out"


def im_thread_id(channel: str, chat_id: str) -> str:
    """`im_lark_<chat_id>` — one thread per IM conversation."""
    return f"{IM_THREAD_PREFIX}{channel}_{chat_id}"


def agent_dm_thread_id(agent_id: str, peer_agent_id: str) -> str:
    """`nx_dm_<agent>_<peer>` — one thread per (agent, peer) pair.

    Both ids, not just the peer: the same owner can have several agents talking
    to the same peer, and the panel lists per agent.
    """
    return f"{AGENT_DM_THREAD_PREFIX}{agent_id}_{peer_agent_id}"


class InboxRecorder:
    """Writes one conversational turn into the inbox record.

    Stateless apart from the source name, so a caller can hold one per channel
    or build one per call. The db handle is injected — this module never
    reaches for ``get_db_client``, which keeps it unit-testable and keeps the
    caller's transaction/handle choices its own (the same reason
    ``ChannelInboxWriter`` took one).
    """

    def __init__(self, source: str, brand_display: str = "") -> None:
        """
        Args:
            source: matches ``MessageSourceHandler.name`` for IM sources
                ("lark", "wechat", …) or "agent_dm" for peer conversation.
            brand_display: human label used in the thread title ("Feishu").
                Defaults to a title-cased source when empty.
        """
        if not source:
            raise ValueError("source must be a non-empty string")
        self._source = source
        self._brand = brand_display or source.title()

    @property
    def source(self) -> str:
        return self._source

    async def record_turn(
        self,
        *,
        db,
        thread_id: str,
        owner_user_id: str,
        agent_id: str,
        counterpart_id: str,
        counterpart_name: str,
        inbound_text: str,
        outbound_text: str = "",
        inbound_attachments: Optional[Sequence[dict]] = None,
        outbound_attachments: Optional[Sequence[dict]] = None,
    ) -> None:
        """Record one turn: what arrived, and what the agent said back.

        Best-effort at the caller's discretion — this re-raises so the caller
        can write its own audit row (``EVENT_INBOX_WRITE_FAILED``), which is
        the contract ``ChannelInboxWriter`` had and the triggers still rely on.

        The outbound row is written only when the agent actually said
        something: a turn that legitimately stayed silent should not leave an
        empty bubble in the user's panel.
        """
        now = utc_now()
        display = counterpart_name if counterpart_name and counterpart_name != "Unknown" else counterpart_id

        await self._ensure_thread(
            db,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            counterpart_id=counterpart_id,
            counterpart_name=display,
            now=now,
        )

        # Inbound and reply are ONE turn but two rows that must sort
        # inbound-then-reply. Written together, a shared `now` gave them an
        # identical created_at, and `ORDER BY created_at` with no tie-break let
        # the reply sort above the message it answered — worst on WeChat, whose
        # messages carry no timestamp of their own. One microsecond apart means
        # created_at alone orders a turn, and utc_now()'s advance between turns
        # keeps turns in completion order.
        await self._insert_message(
            db, thread_id=thread_id, direction=INBOUND,
            sender_id=counterpart_id, sender_name=display,
            content=inbound_text, attachments=inbound_attachments, at=now,
        )
        if outbound_text and outbound_text.strip():
            await self._insert_message(
                db, thread_id=thread_id, direction=OUTBOUND,
                sender_id=agent_id, sender_name="",
                content=outbound_text,
                attachments=outbound_attachments,
                at=now + timedelta(microseconds=1),
            )
            preview = outbound_text
        else:
            preview = inbound_text

        await db.update(
            "inbox_threads",
            {"thread_id": thread_id},
            {
                "last_message_at": now + timedelta(microseconds=1),
                "last_message_preview": (preview or "")[:200],
                "updated_at": now,
            },
        )
        logger.info(f"InboxRecorder[{self._source}]: recorded turn in {thread_id}")

    async def _ensure_thread(
        self,
        db,
        *,
        thread_id: str,
        owner_user_id: str,
        agent_id: str,
        counterpart_id: str,
        counterpart_name: str,
        now,
    ) -> None:
        """Create the thread, or refresh a placeholder name on an existing one.

        The name refresh is not cosmetic. A sender first seen with
        ``sender_name="Unknown"`` falls back to the raw id, and without this the
        panel would show that id forever — for the whole first burst of messages
        from every new contact.

        **The insert may lose a race, and losing is not an error.** `thread_id`
        is the primary key, so two turns opening the same NEW thread both read
        `None` and both insert; the loser used to raise, `record_turn` re-raised,
        and the caller booked `EVENT_INBOX_WRITE_FAILED` — a message simply
        missing from the user's panel. Reachable in ordinary use: debounce
        batches and multi-agent group chats deliver concurrently. So a duplicate
        key is treated as "someone else created it", which is what it means, and
        the refresh runs against their row. Same shape as `wake_signal.bump`.
        """
        title = f"{self._brand}: {counterpart_name}"

        async def _refresh(existing: dict) -> None:
            if counterpart_name and counterpart_name != existing.get("counterpart_name"):
                await db.update(
                    "inbox_threads",
                    {"thread_id": thread_id},
                    {"counterpart_name": counterpart_name, "title": title, "updated_at": now},
                )

        existing = await db.get_one("inbox_threads", {"thread_id": thread_id})
        if existing:
            await _refresh(existing)
            return
        try:
            await db.insert("inbox_threads", {
                "thread_id": thread_id,
                "owner_user_id": owner_user_id,
                "agent_id": agent_id,
                "source": self._source,
                "title": title,
                "counterpart_id": counterpart_id,
                "counterpart_name": counterpart_name,
                "created_at": now,
                "updated_at": now,
            })
        except Exception:  # noqa: BLE001 — narrowed by the re-read below
            # Re-read rather than inspect the driver's error: the duplicate-key
            # exception type differs between aiosqlite and aiomysql, and a
            # dialect-specific `except` here would silently stop catching the
            # race on one of the two backends. If the row is there, the race is
            # what happened; if it is not, the insert failed for a real reason
            # and the original exception is the honest thing to raise.
            raced = await db.get_one("inbox_threads", {"thread_id": thread_id})
            if raced is None:
                raise
            await _refresh(raced)

    async def _insert_message(
        self,
        db,
        *,
        thread_id: str,
        direction: str,
        sender_id: str,
        sender_name: str,
        content: str,
        attachments: Optional[Sequence[dict]],
        at,
        source_message_id: Optional[str] = None,
    ) -> None:
        await db.insert("inbox_thread_messages", {
            "message_id": f"ibx_{uuid.uuid4().hex[:16]}",
            "thread_id": thread_id,
            "direction": direction,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "attachments": json.dumps(list(attachments), ensure_ascii=False) if attachments else None,
            "source_message_id": source_message_id,
            "created_at": at,
        })


async def resolve_owner_for_agent(db, agent_id: str) -> str:
    """The owner a thread belongs to.

    Threads are listed per user, so the record needs the owner even though the
    triggers only hold an agent_id. Goes through the shared repository seam
    rather than a local query, so "who owns this agent" keeps one answer.
    """
    try:
        from xyz_agent_context.repository.agent_repository import AgentRepository

        return await AgentRepository(db).resolve_owner(agent_id) or ""
    except Exception as e:  # noqa: BLE001 — a missing owner degrades the panel, not the turn
        logger.warning(f"InboxRecorder: owner lookup failed for {agent_id}: {e}")
        return ""


__all__ = [
    "InboxRecorder",
    "IM_THREAD_PREFIX",
    "AGENT_DM_THREAD_PREFIX",
    "INBOUND",
    "OUTBOUND",
    "im_thread_id",
    "agent_dm_thread_id",
    "resolve_owner_for_agent",
]
