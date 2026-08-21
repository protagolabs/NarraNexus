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

from xyz_agent_context.agent_framework.nexus_power.contracts.model import ProviderMessage


class NullSteeringInlet:
    """No steering source; always empty."""

    async def drain(self) -> list[ProviderMessage]:
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
    * **Bounded queue, back-pressure not drop.** The transport owns the
      queue and must give it a ``maxsize``, back-pressuring the producer
      when full (await / queue upstream) — never dropping. Capacity is the
      transport's call (local stdin and cloud ``/steer`` differ), so it is
      NOT hardcoded here. ``drain()`` deliberately takes the whole backlog
      and never truncates: dropping queued messages would lose user input
      (iron rule #16), so any bounding belongs at the bounded-queue write
      edge, never in this read.
    """

    def __init__(self, queue: asyncio.Queue[ProviderMessage]) -> None:
        self._queue = queue

    async def drain(self) -> list[ProviderMessage]:
        drained: list[ProviderMessage] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return drained
