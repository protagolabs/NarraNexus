"""
@file_name: steering.py
@author: Bin Liang
@date: 2026-07-29
@description: Step-boundary steering inlets. The DRAIN_STEERING call
site exists from day one; the loop never changes, only which inlet is
mounted. Two implementations: NullSteeringInlet (the default, locked
empty by a contract test) and QueueSteeringInlet (the P4 TriggerInbox
seam — an in-process queue the transport layer feeds). Injection is
append-only by contract (a prefix mutation would void the prompt cache).
"""

from __future__ import annotations

import asyncio

from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ProviderMessage,
    STEER_ID_KEY,
)


class NullSteeringInlet:
    """No steering source; always empty."""

    async def drain(self) -> list[ProviderMessage]:
        return []

    def take_consumed(self) -> list[str]:
        return []


class QueueSteeringInlet:
    """The P4 TriggerInbox seam: drains an in-process queue each step.

    The loop owns exactly one question — ``drain()`` at the step boundary
    — and knows nothing about where the messages came from. The transport
    layer (the runner's stdin reader locally; the executor's steer
    endpoint in the cloud) is the sole writer to the queue, so a new
    producer (team room, owner chat, …) never reaches into the loop: it
    only puts a ProviderMessage on the queue.

    Drain is snapshot-and-empty, never blocking: the loop calls it on
    every step boundary and the empty inlet is the common case, so it must
    return immediately when nothing is queued rather than await a put().
    Whatever arrived *before* this drain is taken; whatever arrives after
    is seen by the next one. Order is the queue's FIFO — the arrival order
    the producers wrote in.

    Injection stays append-only by the SteeringInlet contract: the drained
    messages are appended after the turn's existing prefix (the loop hands
    them to ``ledger.record_steering``), never rewritten into it, so the
    prompt cache prefix is preserved.

    Writer contract (the transport must honour it; not enforceable here):

    * **Event-loop affinity.** ``asyncio.Queue`` is not thread-safe. The
      writer must ``put_nowait`` on the loop that runs this turn. A writer
      on another thread (e.g. a blocking ``stdin`` reader) must hand the
      put back to that loop via ``loop.call_soon_threadsafe(
      queue.put_nowait, msg)`` (or ``run_coroutine_threadsafe``) — a bare
      cross-thread ``put_nowait`` is a latent lost-wakeup the moment any
      awaiting getter is added.
    * **Back-pressure at the durable write edge, not this queue.** This
      queue is an in-flight hand-off of already-admitted messages, so the
      transport (in-process today: the orchestrator's steer channel) leaves
      it unbounded and ``put_nowait`` never blocks. The bound lives one
      layer up, at the ``steer_inbox`` write edge, where a producer that
      outruns the run is back-pressured (``SteerInboxFull``) — never dropped
      (iron rule #16). ``drain()`` likewise takes the whole backlog and never
      truncates. The invariant that keeps this in-flight queue from growing
      is the ORCHESTRATOR's: it must push into the channel at the loop's
      drain rate (one step-boundary's worth), not empty the whole inbox
      backlog at once — that pacing is the steer-routing PR's to guarantee.
    """

    def __init__(self, queue: asyncio.Queue[ProviderMessage]) -> None:
        self._queue = queue
        # steer_inbox row ids of messages drained (consumed) but not yet
        # reported. The loop reads this after each drain to emit a
        # ``steer_consumed`` signal; the producer marks those rows consumed and
        # only THEN advances its cursor — so a message pushed-but-never-drained
        # is never acked (never lost). A private, transport-only key.
        self._consumed_ids: list[str] = []

    async def drain(self) -> list[ProviderMessage]:
        drained: list[ProviderMessage] = []
        while True:
            try:
                msg = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained
            # Strip the platform bookkeeping key (the model never sees it) and
            # record it as consumed. A message without one (tests, a non-steer
            # producer) tracks nothing.
            steer_id = msg.pop(STEER_ID_KEY, None) if isinstance(msg, dict) else None
            if steer_id is not None:
                self._consumed_ids.append(steer_id)
            drained.append(msg)

    def take_consumed(self) -> list[str]:
        """The steer_inbox ids consumed since the last call, then cleared.
        The loop calls this after ``drain`` to report consumption exactly once."""
        ids, self._consumed_ids = self._consumed_ids, []
        return ids
