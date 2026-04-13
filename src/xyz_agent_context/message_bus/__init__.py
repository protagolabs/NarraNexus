"""
@file_name: __init__.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBus package for inter-agent communication

Provides pluggable message bus implementations for agent-to-agent messaging,
channel management, agent discovery, and delivery tracking.
"""

from .cloud_bus import CloudMessageBus
from .local_bus import LocalMessageBus
from .message_bus_service import MessageBusService
from .message_bus_trigger import MessageBusTrigger
from .schemas import BusAgentInfo, BusChannel, BusChannelMember, BusMessage

__all__ = [
    "MessageBusService",
    "LocalMessageBus",
    "CloudMessageBus",
    "MessageBusTrigger",
    "BusMessage",
    "BusChannel",
    "BusChannelMember",
    "BusAgentInfo",
]
