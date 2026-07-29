"""
@file_name: event_adapter.py
@author: Bin Liang
@date: 2026-07-29
@description: LegacyEventAdapter — the ONE place that speaks the legacy
dict event contract (``agent_framework.loop.events``).

Inside the framework everything is a typed ``LoopEvent``; the six
legacy shapes (text delta / thinking / tool_call / tool_call_output /
error / response.done, plus per-step response.usage) are produced here
and nowhere else, so the grey-release coexistence with the claude/codex
drivers costs the platform zero changes. ``tool_arg_delta`` has no
legacy shape yet — the adapter drops it (documented); new-protocol
consumers read the typed stream instead.
"""

from __future__ import annotations

from typing import Any

from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_TEXT_DELTA,
    DATA_TYPE_USAGE,
    ITEM_TYPE_THINKING,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
    TYPE_RAW_RESPONSE_EVENT,
    TYPE_RUN_ITEM_STREAM_EVENT,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.errors import (
    LEGACY_SAFE_ERROR_TYPES,
    ErrorType,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.events import (
    TYPE_ERROR,
    TYPE_STEP_DONE,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    TYPE_TOOL_RESULT,
    TYPE_TOOL_USE,
    TYPE_TURN_DONE,
    LoopEvent,
)


class LegacyEventAdapter:
    """LoopEvent → 0..N legacy dict events."""

    def translate(self, event: LoopEvent) -> list[dict[str, Any]]:
        etype = event.type
        payload = event.payload
        if etype == TYPE_TEXT_DELTA:
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {"type": DATA_TYPE_TEXT_DELTA, "delta": payload["text"]},
                }
            ]
        if etype == TYPE_THINKING_DELTA:
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {"type": ITEM_TYPE_THINKING, "content": payload["text"]},
                }
            ]
        if etype == TYPE_TOOL_USE:
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {
                        "type": ITEM_TYPE_TOOL_CALL,
                        "tool_call_id": payload["call_id"],
                        "tool_name": payload["tool_name"],
                        "arguments": payload.get("args") or {},
                    },
                }
            ]
        if etype == TYPE_TOOL_RESULT:
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {
                        "type": ITEM_TYPE_TOOL_CALL_OUTPUT,
                        "tool_call_id": payload["call_id"],
                        "output": payload.get("content") or "",
                        "status": "completed" if payload.get("ok") else "failed",
                    },
                }
            ]
        if etype == TYPE_STEP_DONE:
            if event.usage is None:
                return []
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_USAGE,
                        "usage": event.usage.as_legacy_dict(),
                    },
                }
            ]
        if etype == TYPE_ERROR:
            error_type = str(payload.get("error_type", ErrorType.UNKNOWN.value))
            if error_type not in LEGACY_SAFE_ERROR_TYPES:
                error_type = ErrorType.INVALID_REQUEST.value
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_ERROR,
                        "error_message": str(payload.get("message", "")),
                        "error_type": error_type,
                    },
                }
            ]
        if etype == TYPE_TURN_DONE:
            usage = event.usage.as_legacy_dict() if event.usage else {}
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_DONE,
                        "usage": usage,
                        "stop_reason": str(payload.get("end_reason", "")).lower(),
                        "model": payload.get("model", ""),
                        "num_turns": payload.get("num_steps", 0),
                    },
                }
            ]
        # tool_arg_delta / compaction have no legacy shape (typed-stream only).
        return []
