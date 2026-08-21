"""
@file_name: steer_channel.py
@author: Bin Liang
@date: 2026-08-21
@description: SteerChannel — the orchestrator's push handle for a live run.

The orchestrator registers one of these in the RunRegistry when it starts a
steerable run and keeps it; when a new message arrives for that run it calls
``push``. The channel renders the injection to a provider message and enqueues
it on a plain asyncio.Queue.

Two deliveries, one channel:
* **in-process** (the loop runs in this process) — the loop's
  ``QueueSteeringInlet`` is built over THIS channel's ``queue``, so a push is
  already where the loop drains: no pump, no copy.
* **subprocess / remote** — the driver runs a small pump that drains the
  channel and writes each message down the runner's steer transport (a stdin
  line locally, the executor's steer endpoint in the cloud); the runner feeds
  its own inlet. That pump lives with the driver, added when the transport is.

``render_injection`` is the single place a ``SteerInjection`` becomes a
provider message. The tag names the source so the model reads a teammate's
room message differently from the owner interjecting — same mechanism,
different wording — while the injected message stays a plain ``user`` message
(append-only, cache-safe).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from xyz_agent_context.schema.steer_schema import SteerInjection

ProviderMessage = Dict[str, Any]

_SOURCE_TAGS = {
    "team": "[teammate {sender} just posted to the room]",
    "owner_chat": "[the owner adds]",
}


def render_injection(inj: SteerInjection) -> ProviderMessage:
    """One ``SteerInjection`` → one provider message. English tag (source
    provenance) + the user's own content untouched."""
    tag = _SOURCE_TAGS.get(inj.source, "[new message from {sender}]").format(
        sender=inj.sender_id
    )
    return {"role": inj.role, "content": f"{tag} {inj.content}"}


class SteerChannel:
    """A live run's steer queue. ``push`` from the orchestrator; the driver
    drains — in-process by sharing ``queue`` with a QueueSteeringInlet, or via
    a pump for the subprocess/remote transport."""

    def __init__(self) -> None:
        self.queue: "asyncio.Queue[ProviderMessage]" = asyncio.Queue()

    async def push(self, inj: SteerInjection) -> None:
        # put_nowait on the run's own event loop — the queue is unbounded here
        # because the bounded, back-pressured edge is steer_inbox (the write
        # edge); this queue is the already-admitted in-flight hand-off. The
        # subprocess pump consumes it with `queue.get()`; the in-process inlet
        # drains it directly.
        self.queue.put_nowait(render_injection(inj))


__all__ = ["SteerChannel", "render_injection", "ProviderMessage"]
