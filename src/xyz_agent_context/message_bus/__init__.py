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

# Register the MessageBus channel handler so chat_module can recognise
# bus-triggered reply tools and render bus rows with a distinct prefix.
# The reply list carries every tool that DELIVERS on a bus turn: the peer
# and room sends (answer-the-peer / team replies) and `notify_owner`
# (Owner Relay). Its live consumer is ChatModule._origin_delivered_text —
# the [DELIVERED-BG]/[NO-REPLY-BG] persistence split whose counts are the
# no-reply metric behind the delivery-fallback decision. Listing only the
# owner-chat tool recorded genuine bus deliveries as NO-REPLY (2026-08-01)
# and poisoned exactly that metric.
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
)

try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="message_bus",
        # Not "Message Bus" — the bus is infrastructure the agent never sees.
        # What it sees is that it is inside NarraNexus talking to a peer agent
        # or a team; WHICH of those is answered by the tool on its desk.
        display_label="NarraNexus",
        user_reply_tool_names=(
            "notify_owner",
            "message_agent",
            "message_team",
        ),
        # Bus sends deliver to peer AGENTS — nothing appears in the
        # owner's web chat. Only the owner-notify tool is owner-visible,
        # so session anchoring and chat-history persistence ignore
        # agent-to-agent traffic (see MessageSourceHandler docstring).
        owner_visible_reply_tool_names=("notify_owner",),
        row_prefix_template="[Bus · from agent={from_agent}]",
    ))
except ValueError:
    pass


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
