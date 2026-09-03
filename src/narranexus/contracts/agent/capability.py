"""
@file_name: capability.py
@author: Bin Liang
@date: 2026-09-03
@description: A capability is a set of stage participations plus metadata (the horizontal axis).

The four tiers are not four mechanisms — they are the same ``Capability``
contract with more or fewer cells of the capability × stage matrix filled:

    TOOL              Act only
    CONTEXT_PROVIDER  Assemble only (instructions / turn context / data)
    MEMORY_KIND       Recall + Commit + Reflect
    MODULE            any stages + tools + tables + triggers

A ``StageParticipant`` is what a capability exposes for one stage. Every
method is optional (structural Protocol): a participant declares only the
cells it fills, and the runtime calls only what is declared. The legacy
``XYZBaseModule`` maps onto this one-to-one (see the module adapter in the
legacy package): ``owns_working_source`` → ``claims_source``,
``get_instructions`` → ``contribute_instructions``,
``get_turn_context`` → ``contribute_turn_context``,
``hook_data_gathering`` → ``gather``, ``get_mcp_config`` /
``get_expressive_tools`` / ``get_disallowed_tools`` → ``contribute_tools``,
``hook_persist_turn`` → ``persist_turn``, ``hook_after_event_execution`` →
``after_turn``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Mapping, Protocol, runtime_checkable

from narranexus.contracts.agent.stages import Stage


class CapabilityTier(str, Enum):
    TOOL = "tool"
    CONTEXT_PROVIDER = "context_provider"
    SKILL = "skill"
    MEMORY_KIND = "memory_kind"
    MODULE = "module"


@dataclass(frozen=True)
class CapabilityMeta:
    """Static description; the cells formerly scattered over seven constant tables."""

    name: str
    tier: CapabilityTier
    display_name: str = ""
    description: str = ""
    priority: int = 100  # lower sorts first in prompt assembly (Awareness=0, Chat=1)
    always_load: bool = False
    is_task_capability: bool = False  # created by decision, not auto-loaded
    provides_chat_history: bool = False
    context_cost_hint: int | None = None  # tokens, order of magnitude
    instance_prefix: str = ""
    requires: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSurface:
    """What a capability adds to (or removes from) the turn's tool surface."""

    mcp_servers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    expressive_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()


class StageParticipant(Protocol):
    """One capability's behaviour in the stages it fills. Every method optional.

    Deliberately NOT ``runtime_checkable``: ``isinstance`` would demand every
    method, and the whole point is that a participant declares only the cells
    it fills. Check presence per stage with ``STAGE_METHODS`` instead.
    """

    # Ingress
    def claims_source(self, working_source: str) -> bool: ...

    # Assemble
    async def gather(self, ctx_data: Any) -> Any: ...
    async def contribute_instructions(self, ctx_data: Any) -> str: ...
    async def contribute_turn_context(self, ctx_data: Any) -> str: ...
    async def contribute_tools(self, ctx_data: Any) -> ToolSurface: ...

    # Commit / Reflect
    async def persist_turn(self, params: Any) -> None: ...
    async def after_turn(self, params: Any) -> Awaitable[None] | None: ...


@runtime_checkable
class Capability(Protocol):
    """A named participant in the turn pipeline."""

    meta: CapabilityMeta

    def participations(self) -> Mapping[Stage, StageParticipant]:
        """Which stages this capability fills, and with what."""
        ...


# Which StageParticipant methods belong to which stage. The runtime and the
# legacy adapter both derive from this single table.
STAGE_METHODS: Mapping[Stage, tuple[str, ...]] = {
    Stage.INGRESS: ("claims_source",),
    Stage.ASSEMBLE: ("gather", "contribute_instructions", "contribute_turn_context", "contribute_tools"),
    Stage.COMMIT: ("persist_turn",),
    Stage.REFLECT: ("after_turn",),
}


__all__ = [
    "STAGE_METHODS",
    "Capability",
    "CapabilityMeta",
    "CapabilityTier",
    "StageParticipant",
    "ToolSurface",
]
