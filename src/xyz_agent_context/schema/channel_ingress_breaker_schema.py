"""
@file_name: channel_ingress_breaker_schema.py
@author:
@date: 2026-08-24
@description: Durable state model for the message-ingress circuit breaker.

The ingress breaker answers a question nothing else on the inbound path
asks: **is this message worth processing at all?** It is the peer of the
real-time-layer Agent breaker (``agent_circuit_breaker_schema``) and of the
subscriber fast-death breaker in ``channel/channel_trigger_base.py``:

- Agent breaker      → "my own turns keep failing, stop scheduling them"
- Fast-death breaker → "my own credential is broken, stop restarting it"
- **Ingress breaker** → "the messages arriving are not worth processing"

One row per session key ``channel|chat_id|sender_id``. Only TIER TRANSITIONS
are written here; the sliding-window counters and content fingerprints that
drive those transitions stay in process memory (a row per inbound message
would be pure write amplification). What must survive a restart is the
cooldown — a 24h isolation that resets on every redeploy is not an
isolation at all.

Binding rules #14/#15: this gates INGRESS of new messages. It never caps,
cancels, or time-limits a running ``agent_loop``, and it never judges the
agent's own model or output — only the inbound traffic shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


def session_key(channel: str, chat_id: str, sender_id: str) -> str:
    """The breaker's identity: "who is speaking, in which room, on which
    channel".

    Single definition so the in-memory cache, the DB row, and the audit
    trail can never key the same conversation three different ways.
    """
    return f"{channel}|{chat_id}|{sender_id}"


class ChannelIngressBreaker(BaseModel):
    """One row of ``channel_ingress_breaker`` — the durable half of one
    session key's breaker state.

    ``tier`` is the escalation memory, deliberately kept across a release
    (see ``_breaker_release`` in ``channel_trigger_base.py`` for the same
    idea on the credential breaker): a session that clears its cooldown and
    immediately misbehaves again must land on the NEXT step of the schedule,
    not restart at the shortest one.

    ``tier`` semantics:
      0 — closed. Traffic flows; nothing is suppressed.
      1+ — has tripped ``tier`` times. ``cooldown_until`` is when the next
           half-open probe is allowed through.
    """

    session_key: str
    channel: str
    agent_id: Optional[str] = None
    chat_id: Optional[str] = None
    sender_id: Optional[str] = None
    tier: int = 0
    cooldown_until: Optional[datetime] = None
    # Messages dropped during the CURRENT cooldown. Reset on each trip so
    # the number answers "how much did this isolation actually absorb?" —
    # the headline figure in the owner alert and the audit row.
    suppressed_count: int = 0
    last_reason: Optional[str] = None
    last_tripped_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
