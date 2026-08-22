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
from typing import Any, Dict, get_args

from loguru import logger

from xyz_agent_context.schema.steer_schema import SteerInjection, SteerSource

ProviderMessage = Dict[str, Any]

_SOURCE_TAGS = {
    "team": "teammate {sender} just posted to the room",
    "owner_chat": "the owner adds",
}
# Every source value must have a tag: the tag is what the prompt layer reads to
# tell a teammate's message from the owner's, so a missing one is a silent
# wrong-wording, not a cosmetic gap. Locked at import so adding a SteerSource
# without a tag fails loudly here rather than degrading in production.
assert set(_SOURCE_TAGS) == set(get_args(SteerSource)), (
    "every SteerSource needs a _SOURCE_TAGS entry"
)

#: Warn when a run's in-flight steer queue grows past this. The queue is
#: unbounded on purpose (back-pressure lives at the steer_inbox write edge —
#: never drop, iron rule #16), so this is a DIAGNOSTIC signal, not a cap: past
#: a step-boundary's worth the orchestrator is pushing faster than the loop
#: drains, which risks one drain overflowing the prompt. Alert, do not truncate.
_STEER_INFLIGHT_WARN = 32


def render_injection(inj: SteerInjection) -> ProviderMessage:
    """One ``SteerInjection`` → one provider message.

    Provenance (which producer) and the user's content are kept STRUCTURALLY
    separate: the platform tag is its own line and the content sits in a
    delimited block, so a teammate whose text contains ``[the owner adds]``
    cannot forge the platform's owner tag (owner and teammate are real,
    distinct authorities here). Stays a plain append-only ``user`` message
    (iron rule #16 / prompt-cache prefix)."""
    tag = _SOURCE_TAGS[inj.source].format(sender=inj.sender_id)
    return {
        "role": inj.role,
        "content": f"[{tag}]\n<message>\n{inj.content}\n</message>",
    }


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
        if self.queue.qsize() > _STEER_INFLIGHT_WARN:
            # The invariant "push at the loop's drain rate" is failing — surface
            # it (diagnostic only; never drop). See _STEER_INFLIGHT_WARN.
            logger.warning(
                f"[steer] in-flight queue at {self.queue.qsize()} "
                f"(> {_STEER_INFLIGHT_WARN}); orchestrator is out-pacing drain"
            )

    def qsize(self) -> int:
        """Pending in-flight injections — for an upstream throttle decision."""
        return self.queue.qsize()


__all__ = ["SteerChannel", "render_injection", "ProviderMessage"]
