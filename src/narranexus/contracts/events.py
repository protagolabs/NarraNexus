"""
@file_name: events.py
@author: Bin Liang
@date: 2026-09-03
@description: Host event names and payload shapes (the ``hooks`` kind's vocabulary).

Naming follows the VS Code API guideline ``onDid|onWill + Verb + Subject``.
Batch 0 declares the vocabulary and the bus; the platform starts emitting at
these observation points in later batches (D9). A name not in ``HOST_EVENTS``
must be ``declare``d on the bus before it can be subscribed to, so typos fail
loud instead of silently never firing.

Contract version: ``API_VERSIONS["events"]``.
"""
from __future__ import annotations

from typing import Any, TypedDict

HOST_EVENTS: tuple[str, ...] = (
    "onWillStartRun",
    "onDidStartRun",
    "onDidCompleteRun",
    "onDidCancelRun",
    "onDidPersistTurn",
    "onDidChangeArtifact",
    "onDidReceiveChannelMessage",
    "onDidReplyChannelMessage",
    "onDidActivatePlugin",
)


class RunEvent(TypedDict):
    """Payload for the ``*Run`` events."""

    run_id: str
    agent_id: str
    user_id: str
    working_source: str


class TurnEvent(TypedDict):
    """Payload for ``onDidPersistTurn``."""

    run_id: str
    agent_id: str
    user_id: str
    event_id: str
    narrative_ids: list[str]


class ArtifactEvent(TypedDict):
    """Payload for ``onDidChangeArtifact``."""

    artifact_id: str
    agent_id: str
    user_id: str
    change: str


class ChannelMessageEvent(TypedDict):
    """Payload for the channel message events."""

    channel: str
    agent_id: str
    conversation_id: str
    message_id: str


class PluginEvent(TypedDict):
    """Payload for ``onDidActivatePlugin``."""

    plugin_id: str
    version: str
    host: str


EventPayload = dict[str, Any]

__all__ = [
    "HOST_EVENTS",
    "RunEvent",
    "TurnEvent",
    "ArtifactEvent",
    "ChannelMessageEvent",
    "PluginEvent",
    "EventPayload",
]
