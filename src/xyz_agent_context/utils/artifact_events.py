"""
@file_name: artifact_events.py
@author: Bin Liang
@date: 2026-05-08
@description: In-process pub/sub for Artifact WebSocket events.

Single-process design: a dict of agent_id -> set of asyncio.Queue. publish() writes
to every queue for that agent. subscribe() returns a fresh queue; unsubscribe()
removes it. Bounded queues drop oldest on overflow to prevent slow clients from
blocking publishers.

Multi-process / multi-worker is out of scope for v1 (Tauri sidecar is one process,
EC2 default deployment is one uvicorn worker — see deploy notes).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Dict, Set


_QUEUE_MAXSIZE = 256


class ArtifactEventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, agent_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[agent_id].add(q)
        return q

    def unsubscribe(self, agent_id: str, queue: asyncio.Queue) -> None:
        bucket = self._subscribers.get(agent_id)
        if bucket is None:
            return
        bucket.discard(queue)
        if not bucket:
            self._subscribers.pop(agent_id, None)

    async def publish(self, agent_id: str, event: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(agent_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # drop oldest to keep latest fresh
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass


_bus_singleton: ArtifactEventBus | None = None


def get_artifact_event_bus() -> ArtifactEventBus:
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = ArtifactEventBus()
    return _bus_singleton
