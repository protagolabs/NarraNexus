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
_NP = "xyz_agent_context.agent_framework.nexus_power.extension_points"
_NP_PROTO = "xyz_agent_context.agent_framework.nexus_power.contracts.protocols"
_MODULE = "xyz_agent_context.module"
_DRIVERS = "xyz_agent_context.agent_framework.providers.driver.drivers"

BUILTIN_MANIFEST_DATA: tuple[dict[str, Any], ...] = (
    {
        "id": "builtin.frameworks.nexus_power",
        "version": "1.0.0",
        "displayName": "NexusPower agent loop",
        "description": "The home-grown agent loop; always available. Declares its strategy seats as extension points.",
        "hosts": ["backend"],
        "provides": {
            "turn.pipeline.act.framework": f"{_FRAMEWORK}:NEXUS_POWER",
            "builtin.frameworks.nexus_power.stop": f"{_NP}:STOP_DEFAULT",
            "builtin.frameworks.nexus_power.compaction": f"{_NP}:COMPACTION_DEFAULT",
            "builtin.frameworks.nexus_power.projector": f"{_NP}:PROJECTOR_DEFAULT",
            "builtin.frameworks.nexus_power.expression": f"{_NP}:EXPRESSION_DEFAULT",
            "builtin.frameworks.nexus_power.policy": [f"{_NP}:POLICY_LAYERS"],
        },
        # The loop's five strategy seats (spec §484): other plugins provide
        # implementations, configuration binds them (NX_BIND__builtin__frameworks__nexus_power__stop=...).
        "declares": {
            "builtin.frameworks.nexus_power.stop": {"arity": "one", "contract": f"{_NP_PROTO}:StopPolicy", "default": "no_more_actions", "doc": "When the loop ends a turn."},
            "builtin.frameworks.nexus_power.compaction": {"arity": "one", "contract": f"{_NP_PROTO}:CompactionPolicy", "default": "tool_result_pruner", "doc": "How the ledger is compacted."},
            "builtin.frameworks.nexus_power.projector": {"arity": "one", "contract": f"{_NP_PROTO}:ContextProjector", "default": "passthrough", "doc": "How the ledger becomes provider messages."},
            "builtin.frameworks.nexus_power.expression": {"arity": "one", "contract": f"{_NP_PROTO}:ExpressionPolicy", "default": "contract", "doc": "Which tools count as the agent speaking."},
            "builtin.frameworks.nexus_power.policy": {"arity": "many", "contract": f"{_NP_PROTO}:PolicyLayer", "doc": "Tool-call policy layers, checked in order."},
        },
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
        "api": {"module": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_NEXUS_PLUGINS_MODULE"]},
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
    {
        "id": "builtin.awareness",
        "version": "1.0.0",
        "displayName": "Awareness",
        "description": "Builtin module AwarenessModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "data_access": 0, "route": 0, "hook": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_AWARENESS"], "agent.capabilities.data_access": ["xyz_agent_context.module.awareness_module.data_access:DATA_ACCESS"], "backend.routes": ["backend.routes.agents.awareness:ROUTES", "backend.routes.agents.profile:ROUTES"], "backend.hooks": ["xyz_agent_context.module.awareness_module.plugin_hooks:HOOKS"]},
        "quality": "gold",
    },
    {
        "id": "builtin.basic_info",
        "version": "1.0.0",
        "displayName": "BasicInfo",
        "description": "Builtin module BasicInfoModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "data_access": 0, "route": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_BASIC_INFO"], "agent.capabilities.data_access": ["xyz_agent_context.module.basic_info_module.data_access:DATA_ACCESS"], "backend.routes": ["backend.routes.agents.narrative:ROUTES"]},
        "quality": "gold",
    },
    {
        "id": "builtin.chat",
        "version": "1.0.0",
        "displayName": "Chat",
        "description": "Builtin module ChatModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "hook": 0, "data_access": 0, "route": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHAT"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHAT"], "backend.hooks": ["xyz_agent_context.module.chat_module.plugin_hooks:HOOKS"], "agent.capabilities.data_access": ["xyz_agent_context.module.chat_module.data_access:DATA_ACCESS"], "backend.routes": ["backend.routes.agents.chat_history:ROUTES"]},
        "quality": "gold",
    },
    {
        "id": "builtin.social_network",
        "version": "1.0.0",
        "displayName": "SocialNetwork",
        "description": "Builtin module SocialNetworkModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "data_access": 0, "route": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_SOCIAL_NETWORK"], "agent.capabilities.data_access": ["xyz_agent_context.module.social_network_module.data_access:DATA_ACCESS"], "backend.routes": ["backend.routes.agents.social_network:ROUTES"]},
        "quality": "gold",
    },
    {
        "id": "builtin.job",
        "version": "1.0.0",
        "displayName": "Job",
        "description": "Builtin module JobModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "data_access": 0, "route": 0, "hook": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_JOB"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_JOB"], "agent.capabilities.data_access": ["xyz_agent_context.module.job_module.data_access:DATA_ACCESS"], "backend.routes": ["backend.routes.agents.jobs:ROUTES", "backend.routes.jobs:ROUTES", "backend.routes.dashboard.jobs:ROUTES"], "backend.hooks": ["xyz_agent_context.module.job_module.plugin_hooks:HOOKS"]},
        "quality": "gold",
    },
    {
        "id": "builtin.skills",
        "version": "1.0.0",
        "displayName": "Skill",
        "description": "Builtin module SkillModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "route": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_SKILLS"], "backend.routes": ["backend.routes.skills:ROUTES"]},
        "quality": "gold",
    },
    {
        "id": "builtin.message_bus",
        "version": "1.0.0",
        "displayName": "MessageBus",
        "description": "Builtin module MessageBusModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_MESSAGE_BUS"]},
        "quality": "gold",
    },
    {
        "id": "builtin.common_tools",
        "version": "1.0.0",
        "displayName": "CommonTools",
        "description": "Builtin module CommonToolsModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_COMMON_TOOLS"]},
        "quality": "gold",
    },
    {
        "id": "builtin.general_memory",
        "version": "1.0.0",
        "displayName": "GeneralMemory",
        "description": "Builtin module GeneralMemoryModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_GENERAL_MEMORY"]},
        "quality": "gold",
    },
    {
        "id": "builtin.home_assistant",
        "version": "1.0.0",
        "displayName": "HomeAssistant",
        "description": "Builtin module HomeAssistantModule.",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "route": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_HOME_ASSISTANT"], "backend.routes": ["backend.routes.home_assistant:ROUTES"], "ingress.channels": ["xyz_agent_context.module.home_assistant_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.channels.lark",
        "version": "1.0.0",
        "displayName": "Lark",
        "description": "Builtin module LarkModule (IM channel: trigger + module + tools). The lark-oapi SDK installs on demand on a slim build.",
        "hosts": ["backend", "mcp", "workers"],
        # Heavy dependency (spec section 843): declared here so a distribution
        # that leaves lark-oapi out of the base install self-heals on first boot
        # (wheels-only into ~/.narranexus/plugin-deps) or boots without Lark.
        "backend": {"pip": ["lark-oapi>=1.4.0,<2.0.0"], "imports": ["lark_oapi"]},
        "install": {"deps": "on_demand"},
        "api": {"module": 0, "trigger": 0, "route": 0, "hook": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHANNELS_LARK"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHANNELS_LARK"], "backend.routes": ["backend.routes.channels.lark:ROUTES"], "backend.hooks": ["xyz_agent_context.module.lark_module.plugin_hooks:HOOKS"], "ingress.channels": ["xyz_agent_context.module.lark_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.channels.slack",
        "version": "1.0.0",
        "displayName": "Slack",
        "description": "Builtin module SlackModule (IM channel: trigger + module + tools).",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "route": 0, "hook": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHANNELS_SLACK"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHANNELS_SLACK"], "backend.routes": ["backend.routes.channels.slack:ROUTES"], "backend.hooks": ["xyz_agent_context.module.slack_module.plugin_hooks:HOOKS"], "ingress.channels": ["xyz_agent_context.module.slack_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.channels.telegram",
        "version": "1.0.0",
        "displayName": "Telegram",
        "description": "Builtin module TelegramModule (IM channel: trigger + module + tools).",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "route": 0, "hook": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHANNELS_TELEGRAM"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHANNELS_TELEGRAM"], "backend.routes": ["backend.routes.channels.telegram:ROUTES"], "backend.hooks": ["xyz_agent_context.module.telegram_module.plugin_hooks:HOOKS"], "ingress.channels": ["xyz_agent_context.module.telegram_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.channels.wechat",
        "version": "1.0.0",
        "displayName": "WeChat",
        "description": "Builtin module WeChatModule (IM channel: trigger + module + tools).",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "route": 0, "hook": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHANNELS_WECHAT"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHANNELS_WECHAT"], "backend.routes": ["backend.routes.channels.wechat:ROUTES"], "backend.hooks": ["xyz_agent_context.module.wechat_module.plugin_hooks:HOOKS"], "ingress.channels": ["xyz_agent_context.module.wechat_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.channels.narramessenger",
        "version": "1.0.0",
        "displayName": "Narramessenger",
        "description": "Builtin module NarramessengerModule (IM channel: trigger + module + tools).",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "route": 0, "hook": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHANNELS_NARRAMESSENGER"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHANNELS_NARRAMESSENGER"], "backend.routes": ["backend.routes.channels.narramessenger:ROUTES"], "backend.hooks": ["xyz_agent_context.module.narramessenger_module.plugin_hooks:HOOKS"], "ingress.channels": ["xyz_agent_context.module.narramessenger_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.channels.discord",
        "version": "1.0.0",
        "displayName": "Discord",
        "description": "Builtin module DiscordModule (IM channel: trigger + module + tools).",
        "hosts": ["backend", "mcp", "workers"],
        "api": {"module": 0, "trigger": 0, "route": 0, "hook": 0, "channel": 0},
        "provides": {"agent.capabilities.modules": ["xyz_agent_context.module.contributions:PLUGIN_CHANNELS_DISCORD"], "ingress.triggers": ["xyz_agent_context.module.contributions:TRIGGERS_CHANNELS_DISCORD"], "backend.routes": ["backend.routes.channels.discord:ROUTES"], "backend.hooks": ["xyz_agent_context.module.discord_module.plugin_hooks:HOOKS"], "ingress.channels": ["xyz_agent_context.module.discord_module.descriptor:CHANNEL"]},
        "quality": "gold",
    },
    {
        "id": "builtin.teams",
        "version": "1.0.0",
        "displayName": "Teams",
        "description": "Agent teams: the /api/teams API and the team bulletin summary worker (feature-level plugin).",
        "hosts": ["backend"],
        "api": {"route": 0, "worker": 0},
        "dependencies": {"builtin.message_bus": ">=1.0", "builtin.chat": ">=1.0"},
        "provides": {
            "backend.routes": ["backend.routes.teams:ROUTES"],
            "backend.workers": ["xyz_agent_context.services.team_summary_worker:WORKERS"],
        },
        "quality": "gold",
    },
)


def build_builtin_manifests(tree: SlotTree) -> tuple[Manifest, ...]:
    """Validate the builtin manifest data against ``tree`` (uncached)."""
    return tuple(parse_manifest(data, tree=tree, allow_builtin=True) for data in BUILTIN_MANIFEST_DATA)


def slot_tree_with_builtins() -> SlotTree:
    """The kernel tree plus every slot a builtin plugin declares (e.g. ``builtin.turn``'s stage slots).

    User-plugin validation (install, discover, publish-check, self-extension)
    must see these: a third-party Recall strategy provides into
    ``turn.pipeline.recall``, which only exists once ``builtin.turn`` declared it.
    """
    tree = build_kernel_slot_tree()
    for manifest in builtin_manifests():
        for slot in manifest.declared_slots():
            if slot.path not in tree:
                tree.declare(slot, create_namespaces=True)
    return tree


@lru_cache(maxsize=1)
def builtin_manifests() -> tuple[Manifest, ...]:
    """Validated builtin manifests against the kernel tree (cached; the data is a constant)."""
    return build_builtin_manifests(build_kernel_slot_tree())


__all__ = ["BUILTIN_MANIFEST_DATA", "build_builtin_manifests", "builtin_manifests", "slot_tree_with_builtins"]
