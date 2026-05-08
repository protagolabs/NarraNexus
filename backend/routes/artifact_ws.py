"""
@file_name: artifact_ws.py
@author: Bin Liang
@date: 2026-05-08
@description: WebSocket endpoint /ws/artifacts/{agent_id} fan-out for artifact events.

Frontend opens one connection per active agent. Server pushes JSON events:
  {"type":"artifact.created"|"artifact.updated"|"artifact.pinned"|"artifact.deleted", ...}

A 30-second ping heartbeat keeps dead clients detectable without waiting for the
next real event.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from xyz_agent_context.utils.artifact_events import get_artifact_event_bus


router = APIRouter()


@router.websocket("/ws/artifacts/{agent_id}")
async def artifact_event_stream(websocket: WebSocket, agent_id: str) -> None:
    """
    Subscribe to artifact events for a specific agent.

    Accepts the WebSocket connection, subscribes to the in-process event bus
    for the given agent_id, then forwards events as JSON messages. Sends a
    {"type": "ping"} heartbeat every 30 seconds when idle.

    Args:
        websocket: The WebSocket connection.
        agent_id: The agent whose artifact events to stream.
    """
    await websocket.accept()
    bus = get_artifact_event_bus()
    queue = bus.subscribe(agent_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.debug(f"artifact ws disconnect agent_id={agent_id}")
    finally:
        bus.unsubscribe(agent_id, queue)
