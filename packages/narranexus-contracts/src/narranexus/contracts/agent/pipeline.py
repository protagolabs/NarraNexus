"""
@file_name: pipeline.py
@author: Bin Liang
@date: 2026-09-03
@description: Stage strategies (the vertical slots) and the per-agent pipeline profile that selects them.

Today fast mode, voice mode, job turns and silent IM ingestion are boolean
knobs scattered across the runtime steps. A ``PipelineProfile`` names them:
one strategy per stage, a budget, and an optional capability filter. Built-in
profiles are ``default``, ``fast``, ``voice``, ``job`` and ``silent``; plugins
contribute more; an agent binds one; a turn may override it.

``StageStrategy`` is structural: a strategy for stage S is any object with an
``async run(inputs) -> S's context value``. The platform's turn runtime is the
only caller, so the argument shape is the platform's ``StageInputs`` (not part
of the public contract yet — alpha).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, Protocol, runtime_checkable

from narranexus.contracts.agent.stages import Budgets, Stage


@runtime_checkable
class StageStrategy(Protocol):
    """One stage's behaviour; bound through the ``turn.pipeline.<stage>`` slot."""

    stage: Stage

    async def run(self, inputs: Any) -> Any: ...


@runtime_checkable
class TurnPipeline(Protocol):
    """The whole turn runtime (slots ``turn`` / ``turn.pipeline``).

    ``run`` executes the seven stages for one turn under ``profile`` and
    yields the platform's progress/response messages until Commit has
    finished (Reflect runs in the background). The message vocabulary is the
    platform's (alpha), like ``StageStrategy.run``'s inputs.
    """

    # An async generator: declared with plain ``def`` so the annotation is the
    # iterator itself, not a coroutine resolving to one.
    def run(self, ingress: Any, profile: "PipelineProfile") -> AsyncIterator[Any]: ...


# The Act stage's strategy (slot ``turn.pipeline.act``) has the shape every
# other stage strategy has; the name exists so the slot tree can point at it.
ActStrategy = StageStrategy


@dataclass(frozen=True)
class CapabilityFilter:
    """Which capabilities take part under a profile (``None`` = all enabled)."""

    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()

    def allows(self, name: str) -> bool:
        if name in self.exclude:
            return False
        return self.include is None or name in self.include


@dataclass(frozen=True)
class PipelineProfile:
    """A named selection of stage strategies plus budgets and a capability filter."""

    id: str
    strategies: Mapping[Stage, str] = field(default_factory=dict)  # stage -> strategy name; missing = default
    budgets: Budgets = field(default_factory=Budgets)
    capability_filter: CapabilityFilter = field(default_factory=CapabilityFilter)
    narrative_persistence: str = "durable"  # "durable" | "ephemeral"

    def strategy_for(self, stage: Stage, default: str = "default") -> str:
        return self.strategies.get(stage, default)

    def with_override(self, override: "TurnOverride") -> "PipelineProfile":
        strategies = dict(self.strategies)
        strategies.update(override.strategies)
        return PipelineProfile(
            id=self.id,
            strategies=strategies,
            budgets=override.budgets or self.budgets,
            capability_filter=override.capability_filter or self.capability_filter,
            narrative_persistence=override.narrative_persistence or self.narrative_persistence,
        )


@dataclass(frozen=True)
class TurnOverride:
    """Per-turn overlay on the agent's profile (the TURN binding layer)."""

    strategies: Mapping[Stage, str] = field(default_factory=dict)
    budgets: Budgets | None = None
    capability_filter: CapabilityFilter | None = None
    narrative_persistence: str | None = None


BUILTIN_PROFILE_IDS: tuple[str, ...] = ("default", "fast", "voice", "job", "silent")

__all__ = [
    "BUILTIN_PROFILE_IDS",
    "ActStrategy",
    "CapabilityFilter",
    "PipelineProfile",
    "StageStrategy",
    "TurnOverride",
    "TurnPipeline",
]
