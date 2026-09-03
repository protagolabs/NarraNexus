"""
@file_name: events.py
@author: Bin Liang
@date: 2026-07-27
@description: Re-export of the agent-loop event contract (canonical home: narranexus.contracts.agent_events).

The constants, TypedDicts and constructors moved to
``narranexus.contracts.agent_events`` in batch 1 of the plugin platform (the
event dict is the framework kind's second contract, next to
``AgentLoopDriver``). Every name is re-exported here unchanged so producers
(adapters, remote driver) and consumers (response_processor, output_transfer)
keep importing from this module and see the same objects. Wire values are
protocol: change them only with an ``API_VERSIONS["agent_events"]`` bump.
"""
from __future__ import annotations

from narranexus.contracts.agent_events import (
    TYPE_RAW_RESPONSE_EVENT,
    TYPE_RUN_ITEM_STREAM_EVENT,
    DATA_TYPE_TEXT_DELTA,
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_USAGE,
    DATA_TYPE_REPLY_DELTA,
    DATA_TYPE_RETRY,
    DATA_TYPE_DONE_SUPERSEDED_KEY,
    ITEM_TYPE_THINKING,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
    ITEM_TYPE_MESSAGE_OUTPUT,
    ITEM_TYPE_PLAN,
    CLI_ERROR_TYPES,
    USAGE_CACHE_READ_KEYS,
    USAGE_CACHE_CREATION_KEY,
    RawResponseData,
    RawResponseEvent,
    RunItem,
    RunItemStreamEvent,
    raw_text_delta_event,
    raw_error_event,
)

__all__ = [
    "TYPE_RAW_RESPONSE_EVENT",
    "TYPE_RUN_ITEM_STREAM_EVENT",
    "DATA_TYPE_TEXT_DELTA",
    "DATA_TYPE_DONE",
    "DATA_TYPE_ERROR",
    "DATA_TYPE_USAGE",
    "DATA_TYPE_REPLY_DELTA",
    "DATA_TYPE_RETRY",
    "DATA_TYPE_DONE_SUPERSEDED_KEY",
    "ITEM_TYPE_THINKING",
    "ITEM_TYPE_TOOL_CALL",
    "ITEM_TYPE_TOOL_CALL_OUTPUT",
    "ITEM_TYPE_MESSAGE_OUTPUT",
    "ITEM_TYPE_PLAN",
    "CLI_ERROR_TYPES",
    "USAGE_CACHE_READ_KEYS",
    "USAGE_CACHE_CREATION_KEY",
    "RawResponseData",
    "RawResponseEvent",
    "RunItem",
    "RunItemStreamEvent",
    "raw_text_delta_event",
    "raw_error_event",
]
