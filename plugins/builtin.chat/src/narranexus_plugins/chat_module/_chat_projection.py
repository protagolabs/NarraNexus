"""
@file_name: _chat_projection.py
@author: Bin Liang
@date: 2026-10-30
@description: What the owner's chat panel shows from one stored ChatModule memory message — the single projection behind simple-chat-history and /chat-latest.

Hoisted out of routes.py so the conversation tab (routes.get_simple_chat_history)
and the background new-reply signal (_chat_latest) call the SAME function: the
signal can never report a row the tab would not show, or miss one it would.
Also home of the timestamp / JSON-field parsers both need and of the chat
memory table name (one constant; _chat_reads imports it).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from narranexus.platform.schema.hook_schema import BUS_PRODUCED_SOURCES

#: The ChatModule memory table (registered in schema_registry).
CHAT_MEMORY_TABLE = "instance_json_format_memory_chat"

#: working_source values produced by MessageBusTrigger for peer-agent (A2A)
#: and team turns. The Activity Log surfaces these — and ONLY these — from a
#: peer-scoped instance (see get_simple_chat_history). Sourced from the enum so
#: a new bus transport updates every consumer at once (see chat_module).
A2A_TEAM_SOURCES = BUS_PRODUCED_SOURCES

#: working_source values that represent a real human typing into a chat UI (as
#: opposed to an IM channel trigger, scheduled job, or peer-agent call). For
#: these the "user" side is a genuine user message and MUST be surfaced.
#: "manyfold" — Manyfold's OpenAI-compat endpoint feeds the user input via the
#: same input_content field as CHAT, so its row is a real user message (the
#: 2026-05-26 symptom was the native UI showing only the agent's replies).
USER_FACING_SOURCES = ("chat", "manyfold")


def parse_timestamp(ts: str) -> datetime:
    """Parse various timestamp formats into datetime objects"""
    if not ts:
        return datetime.min
    try:
        ts_normalized = ts.replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(ts_normalized)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (ValueError, AttributeError):
            pass

        for fmt in [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ]:
            try:
                dt = datetime.strptime(ts, fmt)
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                return dt
            except ValueError:
                continue

        logger.warning(f"Unable to parse timestamp: {ts}")
        return datetime.min
    except Exception as e:
        logger.warning(f"Error parsing timestamp {ts}: {e}")
        return datetime.min


def parse_json_field(value: Any, default: Any) -> Any:
    """Parse database fields that may be JSON strings"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


def project_message(msg: Dict[str, Any], peer_scoped: bool) -> Optional[Dict[str, Any]]:
    """Project one stored ChatModule memory message into the row the chat panel
    shows, or None when it is not shown at all.

    The single source of truth for what the owner's panel sees from a memory
    message — used by the simple-chat-history route (every row) and by
    _chat_latest (only the newest conversation row's timestamp), so the
    background "new reply" signal can never drift from the conversation tab.
    """
    meta_data = msg.get("meta_data", {})
    working_source = meta_data.get("working_source", "chat")
    role = msg.get("role", "unknown")

    # Peer-scoped (A2A / team) instances contribute
    # ACTIVITY only: their a2a / message_bus rows, which the
    # block below collapses to a compact marker. Any other
    # source (a user-facing chat, an IM channel) belonging
    # to a non-owner scope is dropped here, never surfaced.
    if peer_scoped and working_source not in A2A_TEAM_SOURCES:
        return None

    # For non-user-facing sources (job/lark/slack/
    # telegram/message_bus/etc), only show assistant
    # messages — the "user" side is the trigger
    # prompt, not something a human typed.
    if (
        working_source not in USER_FACING_SOURCES
        and role != "assistant"
    ):
        return None

    timestamp = meta_data.get("timestamp") or msg.get("created_at")
    message_type = meta_data.get("message_type", "chat")

    # Privacy guard for IM channels: agent replies sent
    # via platform tools (tg_cli sendMessage, slack_cli
    # chat.postMessage, lark_cli +messages-send) live in
    # the same instance memory as chat replies so the
    # agent's NEXT turn can still see them (long-term
    # memory). But surfacing the raw IM reply text to
    # the owner's chat panel mixes two contexts:
    #   (a) "owner ↔ agent direct chat"
    #   (b) "agent ↔ third party on IM"
    # The frontend chat panel is for (a); routine IM
    # chatter should stay on the IM platform. We
    # replace the content with a one-line activity
    # marker AND override ``message_type`` to
    # ``"activity"`` so the frontend renders it
    # compactly (small centered italic line) rather
    # than as a full agent reply bubble — see
    # ``ChatPanel.tsx`` ``if item.messageType ===
    # 'activity'`` branch.
    #
    # Carve-out: when the agent explicitly called
    # ``send_message_to_user_directly`` / ``notify_owner``
    # during the turn (the "tell owner about this important
    # thing" path the iron rules carve out), the writer
    # stashes that content on
    # ``meta_data.owner_notify_content``. We surface it
    # VERBATIM so the owner DOES see the notification while
    # routine IM/peer chatter stays hidden. Which tab it
    # lands in depends on scope — see the branch below.
    content = msg.get("content", "")
    # Privacy-collapse rule applies only to truly-IM
    # / background sources where the assistant reply
    # was meant for a third party, not for the owner
    # viewing this chat panel. Manyfold's assistant
    # reply IS the user-facing answer, so leave it
    # alone.
    if (
        working_source not in USER_FACING_SOURCES
        and role == "assistant"
    ):
        owner_notify = meta_data.get("owner_notify_content", "")
        if owner_notify:
            content = owner_notify
            # Owner scope: the agent's own IM turn notifying
            # its owner — keep it in the conversation tab
            # (message_type stays 'chat'), the long-standing
            # behaviour. Peer scope (A2A/team): the
            # conversation tab never reads peer instances and
            # the activity stream keeps only
            # message_type=='activity', so a 'chat'-typed row
            # would vanish from BOTH tabs. Mark it 'activity'
            # (content stays verbatim, NOT collapsed) so the
            # Activity Log shows the agent's report.
            if peer_scoped:
                message_type = "activity"
        else:
            content = f"Background activity ({working_source})"
            # Force compact rendering on the frontend.
            # Without this the row keeps the writer's
            # default message_type and renders as a
            # full chat bubble — observed 2026-05-13.
            message_type = "activity"

    return {
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "working_source": working_source,
        "message_type": message_type,
        "event_id": meta_data.get("event_id"),
        # The seeded first-run greeting: the client folds the
        # same greeting into its session on the first send and
        # must recognise this row as that greeting (the two
        # texts differ once the client localises its copy).
        "bootstrap": bool(meta_data.get("bootstrap")),
        "attachments": msg.get("attachments"),
    }
