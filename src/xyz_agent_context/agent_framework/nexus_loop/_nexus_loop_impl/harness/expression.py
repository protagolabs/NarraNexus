"""
@file_name: expression.py
@author: Bin Liang
@date: 2026-07-29
@description: The monologue/expression contract's default implementation.

Every other harness treats assistant text as the user-facing reply;
here text is the agent's private thinking and reaching the outside
world requires an expressive tool call. The framework OWNS no
expression tools (they are platform-granted capabilities, injected as a
name list via TurnOptions.expressive_tools); an empty list is a legal
state — an agent without channels is mute, not broken.

This class is the single adjudicator: event tagging, expressiveness
checks and organic-reply statistics all come from here, so the
semantics never scatter into if-statements across the loop.
"""

from __future__ import annotations

from typing import Sequence

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LoopEvent
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolCall


class ExpressionContract:
    """Name-list based ExpressionPolicy (per-agent customization = a new
    implementation swapped into the assembly)."""

    def __init__(self, expressive_tools: frozenset[str]) -> None:
        self._names = expressive_tools

    def is_expressive(self, name: str) -> bool:
        return name in self._names

    def tag_text_event(self, event: LoopEvent) -> LoopEvent:
        """Guarantee the monologue stamp on text/thinking increments
        (idempotent; the ledger already stamps its own emissions)."""
        if event.payload.get("monologue") is True:
            return event
        return LoopEvent(
            track=event.track,
            seq=event.seq,
            type=event.type,
            payload={**event.payload, "monologue": True},
            usage=event.usage,
        )

    def turn_had_expression(self, tool_calls_seen: Sequence[ToolCall]) -> bool:
        return any(self.is_expressive(call.name) for call in tool_calls_seen)
