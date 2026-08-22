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
import secrets
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
    separate AND the separation is unforgeable: the platform tag is the leading
    line, and the content sits inside a ``<message NONCE>…</message NONCE>``
    block whose NONCE is freshly randomised here, per render. Owner and teammate
    are real, distinct authorities (the agent holds shell / file / MCP tools), so
    a teammate must never be able to make the model read their words as the
    owner's. Because the sender cannot predict the nonce, they cannot write a
    matching ``</message NONCE>`` to close the block early — any ``</message>``
    in their text stays trapped INSIDE the block as literal content. The
    invariant the prompt layer trusts: the only ``[…]`` tag line that sits
    OUTSIDE every message block is the one this function emitted. Content is
    passed byte-for-byte (never escaped or clipped — iron rule #16); the nonce,
    not mangling the content, is what makes the boundary hold. Stays a plain
    append-only ``user`` message (prompt-cache prefix)."""
    tag = _SOURCE_TAGS[inj.source].format(sender=inj.sender_id)
    # 8 hex chars (32 bits) of unpredictable delimiter — the project's ID idiom
    # (secrets.token_hex(4)). A per-render nonce, not derived from any
    # sender-influenced field (msg_id could be the sender's own message id), so
    # it cannot be precomputed to forge a block close.
    nonce = secrets.token_hex(4)
    return {
        "role": inj.role,
        "content": (
            f"[{tag}]\n<message {nonce}>\n{inj.content}\n</message {nonce}>"
        ),
    }


class SteerChannel:
    """A live run's steer queue. ``push`` from the orchestrator; the driver
    drains — in-process by sharing ``queue`` with a QueueSteeringInlet, or via
    a pump for the subprocess/remote transport."""

    def __init__(
        self, run_id: str | None = None, agent_id: str | None = None
    ) -> None:
        # Identity is optional so tests can construct a bare channel, but the
        # orchestrator passes both: without them the overflow warning below
        # names a queue depth and nothing else, so on-call cannot tell WHICH of
        # a process's many concurrent runs is the one out-pacing its drain.
        self.run_id = run_id
        self.agent_id = agent_id
        self.queue: "asyncio.Queue[ProviderMessage]" = asyncio.Queue()

    async def push(self, inj: SteerInjection) -> None:
        # put_nowait on the run's own event loop — the queue is unbounded here
        # because the bounded, back-pressured edge is steer_inbox (the write
        # edge); this queue is the already-admitted in-flight hand-off. The
        # subprocess pump consumes it with `queue.get()`; the in-process inlet
        # drains it directly.
        self.queue.put_nowait(render_injection(inj))
        if self.queue.qsize() == _STEER_INFLIGHT_WARN + 1:
            # EDGE-triggered (== threshold+1), not level (> threshold): a single
            # backlog drain can reach MAX_UNCONSUMED_PER_RUN (500), and warning
            # on every push past 32 would emit hundreds of interleaved lines and
            # drown out the very signal when it matters most. Fire once on the
            # upward crossing. Diagnostic only; never drop. See
            # _STEER_INFLIGHT_WARN.
            logger.warning(
                f"[steer] run={self.run_id} agent={self.agent_id} in-flight "
                f"queue crossed {_STEER_INFLIGHT_WARN} "
                f"(now {self.queue.qsize()}); orchestrator is out-pacing drain"
            )

    def qsize(self) -> int:
        """Pending in-flight injections — for an upstream throttle decision."""
        return self.queue.qsize()


__all__ = ["SteerChannel", "render_injection", "ProviderMessage"]
