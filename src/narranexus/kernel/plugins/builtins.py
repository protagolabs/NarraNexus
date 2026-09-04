"""
@file_name: builtins.py
@author: Bin Liang
@date: 2026-09-03
@description: The host's own plugins, declared as manifests (D4: builtins are plugins too).

Explicit registration, not discovery (Python Packaging guide: explicit is
deterministic, greppable and fast). Each manifest's ``provides`` names the
``Contribution`` constants the legacy modules export; the loader registers
exactly those, and import-time registration of the same objects is idempotent.

Batch 0 lists the three kinds already on ``Registry[T]``: agent-loop
frameworks, provider drivers, memory kinds. Later batches add a manifest per
extracted builtin (channels, modules, ui, ...) — one entry here per plugin.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from narranexus.kernel.plugins.manifest import Manifest, parse_manifest
from narranexus.kernel.plugins.slots import SlotTree, build_kernel_slot_tree

_FRAMEWORK = "xyz_agent_context.agent_framework"
_DRIVERS = "xyz_agent_context.agent_framework.providers.driver.drivers"

BUILTIN_MANIFEST_DATA: tuple[dict[str, Any], ...] = (
    {
        "id": "builtin.frameworks.nexus_power",
        "version": "1.0.0",
        "displayName": "NexusPower agent loop",
        "description": "The home-grown agent loop; always available.",
        "hosts": ["backend"],
        "provides": {"turn.pipeline.act.framework": f"{_FRAMEWORK}:NEXUS_POWER"},
        "quality": "gold",
    },
    {
        "id": "builtin.frameworks.claude_code",
        "version": "1.0.0",
        "displayName": "Claude Code agent loop",
        "description": "Claude Agent SDK driver; SDK installed on demand on the local build.",
        "hosts": ["backend"],
        "provides": {"turn.pipeline.act.framework": f"{_FRAMEWORK}:CLAUDE_CODE"},
        "install": {"deps": "on_demand"},
        "quality": "gold",
    },
    {
        "id": "builtin.frameworks.codex_cli",
        "version": "1.0.0",
        "displayName": "Codex agent loop",
        "description": "OpenAI Codex SDK driver; SDK installed on demand on the local build.",
        "hosts": ["backend"],
        "provides": {"turn.pipeline.act.framework": f"{_FRAMEWORK}:CODEX_CLI"},
        "install": {"deps": "on_demand"},
        "quality": "gold",
    },
    {
        "id": "builtin.providers",
        "version": "1.0.0",
        "displayName": "LLM providers",
        "description": "Provider drivers: custom anthropic/openai, NetMind, Yunwu, OpenRouter, OAuth subscriptions, system pool.",
        "hosts": ["backend", "mcp", "workers"],
        "provides": {
            "model.providers": [
                f"{_DRIVERS}.custom_anthropic:CONTRIBUTION",
                f"{_DRIVERS}.custom_openai:CONTRIBUTION",
                f"{_DRIVERS}.netmind:CONTRIBUTION",
                f"{_DRIVERS}.netmind_free:CONTRIBUTION",
                f"{_DRIVERS}.yunwu:CONTRIBUTION",
                f"{_DRIVERS}.openrouter:CONTRIBUTION",
                f"{_DRIVERS}.claude_oauth:CONTRIBUTION",
                f"{_DRIVERS}.codex_oauth:CONTRIBUTION",
                f"{_DRIVERS}.system:CONTRIBUTIONS",
            ]
        },
        "quality": "gold",
    },
    {
        "id": "builtin.llm_clients",
        "version": "1.0.0",
        "displayName": "Helper LLM clients",
        "description": "anthropic / openai / cli protocol clients for the helper-LLM slot.",
        "hosts": ["backend", "mcp", "workers"],
        "provides": {"model.clients": [f"{_FRAMEWORK}.llm.helper_sdk:CONTRIBUTIONS"]},
        "quality": "gold",
    },
    {
        "id": "builtin.memory_kinds",
        "version": "1.0.0",
        "displayName": "Memory kinds",
        "description": "event / bus / narrative / entity / job / observation memory kinds.",
        "hosts": ["backend", "mcp", "workers"],
        "provides": {"agent.capabilities.memory_kinds": ["xyz_agent_context.memory.specs:CONTRIBUTIONS"]},
        "quality": "gold",
    },
    {
        "id": "builtin.nexus_plugins_module",
        "version": "1.0.0",
        "displayName": "Nexus Plugins Module",
        "description": "Agent self-extension: scaffold, test, register and observe plugins (local only).",
        "hosts": ["backend", "mcp"],
        "protected": True,
        "quality": "gold",
    },
    {
        "id": "builtin.turn",
        "version": "1.0.0",
        "displayName": "Turn Pipeline",
        "description": "The seven-stage turn pipeline: default stage strategies and the builtin profiles.",
        "hosts": ["backend"],
        "api": {"stage_strategy": 0, "pipeline_profile": 0, "agent": 0},
        "provides": {
            "turn.pipeline": "narranexus.platform.turn.pipeline:PIPELINE_CONTRIBUTION",
            "turn.pipeline.ingress": ["narranexus.platform.turn.stages:INGRESS"],
            "turn.pipeline.recall": ["narranexus.platform.turn.stages:RECALL"],
            "turn.pipeline.compose": ["narranexus.platform.turn.stages:COMPOSE"],
            "turn.pipeline.assemble": ["narranexus.platform.turn.stages:ASSEMBLE"],
            "turn.pipeline.act": ["narranexus.platform.turn.stages:ACT"],
            "turn.pipeline.commit": ["narranexus.platform.turn.stages:COMMIT"],
            "turn.pipeline.reflect": ["narranexus.platform.turn.stages:REFLECT"],
            "turn.profiles": ["narranexus.platform.turn.profiles:PROFILE_CONTRIBUTIONS"],
        },
        "declares": {
            path: {"arity": "many", "contract": "narranexus.contracts.agent.pipeline:StageStrategy", "doc": f"{path.rsplit('.', 1)[1].title()} stage strategies; a profile names one."}
            for path in ("turn.pipeline.ingress", "turn.pipeline.recall", "turn.pipeline.compose", "turn.pipeline.assemble", "turn.pipeline.commit", "turn.pipeline.reflect")
        },
        "quality": "gold",
    },
)


def build_builtin_manifests(tree: SlotTree) -> tuple[Manifest, ...]:
    """Validate the builtin manifest data against ``tree`` (uncached)."""
    return tuple(parse_manifest(data, tree=tree, allow_builtin=True) for data in BUILTIN_MANIFEST_DATA)


@lru_cache(maxsize=1)
def builtin_manifests() -> tuple[Manifest, ...]:
    """Validated builtin manifests against the kernel tree (cached; the data is a constant)."""
    return build_builtin_manifests(build_kernel_slot_tree())


__all__ = ["BUILTIN_MANIFEST_DATA", "build_builtin_manifests", "builtin_manifests"]
