"""
@file_name: scheduling_channel.py
@author: Bin Liang
@date: 2026-07-29
@description: Scheduling channel (P3/P4 seat) — the loop's scheduling
primitives as tools.

``update_plan`` (P3): the plan ledger's write entry — plan events land
on the ui track and the current plan re-injects each step (industry
consensus: durable rules live where compaction cannot eat them).
``sleep`` (P4): self-scheduling — the turn closes with
``EndReason.SUSPENDED`` and the platform's control-plane timer wakes it
(container death does not lose the wake; combined with a self-message
channel this yields "follow up later" emergent behaviour).
"""

from __future__ import annotations

from typing import Any

from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class SchedulingChannel:
    """update_plan / sleep as a ToolChannel (P3/P4)."""

    def __init__(self, timer_endpoint: str | None) -> None:
        self._timer_endpoint = timer_endpoint

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError("SchedulingChannel ships in P3/P4")

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("SchedulingChannel ships in P3/P4")

    async def refresh(self) -> bool:
        raise NotImplementedError("SchedulingChannel ships in P3/P4")
