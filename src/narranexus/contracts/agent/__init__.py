"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: The agent contracts — the vertical (turn pipeline) and horizontal (capabilities) axes.

An agent turn is a fixed sequence of seven stages (``stages``); what an agent
*has* is a set of capabilities that participate in some of those stages
(``capability``); which strategy runs each stage is a per-agent pipeline
profile (``pipeline``); the whole agent is one value object (``agent_spec``).
Stage observation hooks are declared in ``events``.

Everything here is a value type or a structural Protocol: the platform's turn
runtime implements the stages, plugins implement capabilities and strategies,
and neither imports the other. Contract version: ``API_VERSIONS["agent"]``.
"""
from __future__ import annotations

from narranexus.contracts.agent.agent_spec import AgentSpec, CapabilitySet, ModelIdentity, Persona
from narranexus.contracts.agent.capability import (
    Capability,
    CapabilityMeta,
    CapabilityTier,
    StageParticipant,
    ToolSurface,
)
from narranexus.contracts.agent.events import STAGE_HOOKS, hook_name
from narranexus.contracts.agent.pipeline import CapabilityFilter, PipelineProfile, StageStrategy, TurnOverride
from narranexus.contracts.agent.stages import (
    STAGES,
    ActContext,
    AssembleContext,
    Budgets,
    CommitContext,
    ComposeContext,
    IngressContext,
    RecallContext,
    ReflectContext,
    Stage,
)

__all__ = [
    "STAGES",
    "STAGE_HOOKS",
    "ActContext",
    "AgentSpec",
    "AssembleContext",
    "Budgets",
    "Capability",
    "CapabilityFilter",
    "CapabilityMeta",
    "CapabilitySet",
    "CapabilityTier",
    "CommitContext",
    "ComposeContext",
    "IngressContext",
    "ModelIdentity",
    "Persona",
    "PipelineProfile",
    "RecallContext",
    "ReflectContext",
    "Stage",
    "StageParticipant",
    "StageStrategy",
    "ToolSurface",
    "TurnOverride",
    "hook_name",
]
