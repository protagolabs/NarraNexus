"""
@file_name: contributions.py
@author: Bin Liang
@date: 2026-09-04
@description: The builtin modules as ``agent.capabilities.modules`` contributions — the single table the platform derives module_registry, MCP ports and the always-load list from.

Each entry names the module class lazily (the module package is imported
only when the factory runs) and carries the metadata the platform used to
keep in hard-coded lists: ``channel``. Everything else the platform knows about
a module (always_load, display, decision meta, instance prefix, role) is the
module's own ``ModuleConfig`` (batch 5b). No module owns a port: every module server is mounted by path on
the single MCP host (``module/base.py`` ``mcp_server_url``). ``builtins.py`` names the same objects from one manifest per
module, so disabling a builtin plugin removes its row from every derived
view at boot.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from narranexus.contracts.trigger import TriggerSpec
from narranexus.kernel.plugins.registry import Contribution

MODULES_SLOT = "agent.capabilities.modules"


@dataclass(frozen=True)
class ModuleSpec:
    package: str  # narranexus.platform.module_system.<package>
    class_name: str
    plugin_id: str

    @property
    def channel(self) -> bool:
        return self.plugin_id.startswith("builtin.channels.")

    def load_class(self) -> type:
        # The class lives in the leaf named like its package (chat_module/chat_module.py);
        # several packages deliberately do not re-export it from __init__.
        leaf = importlib.import_module(f"narranexus_plugins.{self.package}.{self.package}")
        return getattr(leaf, self.class_name)

    def contribution(self) -> Contribution[type]:
        return Contribution(
            self.class_name,
            self.load_class,
            meta={"plugin_id": self.plugin_id, "channel": self.channel},
        )


MODULE_SPECS: tuple[ModuleSpec, ...] = (
    ModuleSpec("awareness_module", "AwarenessModule", "builtin.awareness"),
    ModuleSpec("basic_info_module", "BasicInfoModule", "builtin.basic_info"),
    ModuleSpec("chat_module", "ChatModule", "builtin.chat"),
    ModuleSpec("social_network_module", "SocialNetworkModule", "builtin.social_network"),
    ModuleSpec("job_module", "JobModule", "builtin.job"),
    ModuleSpec("skill_module", "SkillModule", "builtin.skills"),
    ModuleSpec("message_bus_module", "MessageBusModule", "builtin.message_bus"),
    ModuleSpec("common_tools_module", "CommonToolsModule", "builtin.common_tools"),
    ModuleSpec("general_memory_module", "GeneralMemoryModule", "builtin.general_memory"),
    ModuleSpec("home_assistant_module", "HomeAssistantModule", "builtin.home_assistant"),
    ModuleSpec("nexus_plugins_module", "NexusPluginsModule", "builtin.nexus_plugins_module"),
    ModuleSpec("lark_module", "LarkModule", "builtin.channels.lark"),
    ModuleSpec("slack_module", "SlackModule", "builtin.channels.slack"),
    ModuleSpec("telegram_module", "TelegramModule", "builtin.channels.telegram"),
    ModuleSpec("wechat_module", "WeChatModule", "builtin.channels.wechat"),
    ModuleSpec("narramessenger_module", "NarramessengerModule", "builtin.channels.narramessenger"),
    ModuleSpec("discord_module", "DiscordModule", "builtin.channels.discord"),
)

BY_PLUGIN: dict[str, tuple[ModuleSpec, ...]] = {}


def module_class_for(plugin_id: str) -> type:
    """The module class a builtin plugin contributes (its ``api.module_class()`` facade delegates here).

    KeyError names the plugin when it contributes no module — a clear error
    instead of the StopIteration the seventeen inline copies used to raise.
    """
    specs = BY_PLUGIN.get(plugin_id)
    if not specs:
        raise KeyError(f"{plugin_id} contributes no module (agent.capabilities.modules)")
    return specs[0].load_class()
for _spec in MODULE_SPECS:
    BY_PLUGIN.setdefault(_spec.plugin_id, ())
    BY_PLUGIN[_spec.plugin_id] = BY_PLUGIN[_spec.plugin_id] + (_spec,)

# One Contribution object per module (identity matters: manifest-driven registration must be idempotent).
CONTRIBUTIONS: dict[str, Contribution[type]] = {s.class_name: s.contribution() for s in MODULE_SPECS}


def contributions_for(plugin_id: str) -> tuple[Contribution[type], ...]:
    return tuple(CONTRIBUTIONS[s.class_name] for s in BY_PLUGIN[plugin_id])


def spec_for(class_name: str) -> ModuleSpec:
    return next(s for s in MODULE_SPECS if s.class_name == class_name)


# ---------------------------------------------------------------- triggers
# ``ingress.triggers``: what each builtin plugs into the ingress hosts. The
# class is named lazily (``class_ref``) so a channel whose optional dependency
# is absent (matrix-nio, ...) isolates itself instead of breaking the map —
# the same per-channel import isolation channel_trigger_map.py always had.
TRIGGERS_SLOT = "ingress.triggers"
_MOD = "narranexus_plugins"

TRIGGER_SPECS: tuple[tuple[str, TriggerSpec], ...] = (
    ("builtin.channels.lark", TriggerSpec("lark", f"{_MOD}.lark_module.lark_trigger:LarkTrigger")),
    ("builtin.channels.slack", TriggerSpec("slack", f"{_MOD}.slack_module.slack_trigger:SlackTrigger")),
    ("builtin.channels.telegram", TriggerSpec("telegram", f"{_MOD}.telegram_module.telegram_trigger:TelegramTrigger")),
    ("builtin.channels.discord", TriggerSpec("discord", f"{_MOD}.discord_module.discord_trigger:DiscordTrigger")),
    ("builtin.channels.wechat", TriggerSpec("wechat", f"{_MOD}.wechat_module.wechat_trigger:WeChatTrigger")),
    # The "narramessenger" channel is served by the Direct-Matrix adapter.
    ("builtin.channels.narramessenger", TriggerSpec("narramessenger", f"{_MOD}.narramessenger_module.matrix_trigger:MatrixTrigger")),
    # The job clock runs as a worker of the workers supervisor (bare name "jobs":
    # run.sh / compose address it with --only/--exclude).
    ("builtin.job", TriggerSpec("jobs", f"{_MOD}.job_module.job_trigger:JobTrigger", host="workers", kwargs={"poll_interval": 60, "max_workers": 5})),
    # The A2A protocol server (Google Agent-to-Agent) the module runner serves on demand.
    ("builtin.chat", TriggerSpec("a2a", f"{_MOD}.chat_module.chat_trigger:A2AServer", host="api")),
)

TRIGGER_CONTRIBUTIONS: dict[str, Contribution[TriggerSpec]] = {
    spec.name: Contribution(spec.name, (lambda s=spec: s), meta={"host": spec.host, "class_ref": spec.class_ref})
    for _, spec in TRIGGER_SPECS
}


def trigger_contributions_for(plugin_id: str) -> tuple[Contribution[TriggerSpec], ...]:
    return tuple(TRIGGER_CONTRIBUTIONS[spec.name] for pid, spec in TRIGGER_SPECS if pid == plugin_id)


def channel_trigger_specs() -> tuple[TriggerSpec, ...]:
    """Registration INTENT for the channels supervisor (independent of what imports here)."""
    return tuple(spec for _, spec in TRIGGER_SPECS if spec.host == "channels")


# ``agent.capabilities.data_access``: AgentDataStore bodies (owner, "pkg.mod:DATA_ACCESS").
DATA_ACCESS_SLOT = "agent.capabilities.data_access"
DATA_ACCESS_SPECS: tuple[tuple[str, str], ...] = (
    ("builtin.awareness", f"{_MOD}.awareness_module.data_access:DATA_ACCESS"),
    ("builtin.social_network", f"{_MOD}.social_network_module.data_access:DATA_ACCESS"),
    ("builtin.basic_info", f"{_MOD}.basic_info_module.data_access:DATA_ACCESS"),
    ("builtin.job", f"{_MOD}.job_module.data_access:DATA_ACCESS"),
    ("builtin.chat", f"{_MOD}.chat_module.data_access:DATA_ACCESS"),
)


def _resolve_symbol(ref: str):
    module_path, attr = ref.rsplit(":", 1)
    return getattr(importlib.import_module(module_path), attr)


# ``ingress.channels``: one ChannelDescriptor per channel (owner, "pkg.mod:CHANNEL").
CHANNELS_SLOT = "ingress.channels"
CHANNEL_SPECS: tuple[tuple[str, str], ...] = (
    ("builtin.channels.lark", f"{_MOD}.lark_module.descriptor:CHANNEL"),
    ("builtin.channels.slack", f"{_MOD}.slack_module.descriptor:CHANNEL"),
    ("builtin.channels.telegram", f"{_MOD}.telegram_module.descriptor:CHANNEL"),
    ("builtin.channels.wechat", f"{_MOD}.wechat_module.descriptor:CHANNEL"),
    ("builtin.channels.narramessenger", f"{_MOD}.narramessenger_module.descriptor:CHANNEL"),
    ("builtin.channels.discord", f"{_MOD}.discord_module.descriptor:CHANNEL"),
    ("builtin.home_assistant", f"{_MOD}.home_assistant_module.descriptor:CHANNEL"),
)


# ``backend.hooks`` implementations builtins ship (owner, "pkg.mod:HOOKS").
HOOK_SPECS: tuple[tuple[str, str], ...] = (
    ("builtin.chat", f"{_MOD}.chat_module.plugin_hooks:HOOKS"),
    ("builtin.job", f"{_MOD}.job_module.plugin_hooks:HOOKS"),
    ("builtin.awareness", f"{_MOD}.awareness_module.plugin_hooks:HOOKS"),
    # Manyfold credential inventory, in the order the route always listed them.
    ("builtin.channels.telegram", f"{_MOD}.telegram_module.plugin_hooks:HOOKS"),
    ("builtin.channels.discord", f"{_MOD}.discord_module.plugin_hooks:HOOKS"),
    ("builtin.channels.slack", f"{_MOD}.slack_module.plugin_hooks:HOOKS"),
    ("builtin.channels.wechat", f"{_MOD}.wechat_module.plugin_hooks:HOOKS"),
    ("builtin.channels.lark", f"{_MOD}.lark_module.plugin_hooks:HOOKS"),
    ("builtin.channels.narramessenger", f"{_MOD}.narramessenger_module.plugin_hooks:HOOKS"),
)

# Services builtins expose on ``Registries.services`` (owner, "pkg.mod:SERVICES"),
# each a tuple of (ServiceRef, impl); see kernel/plugins/service_refs.py.
SERVICE_SPECS: tuple[tuple[str, str], ...] = (
    ("builtin.skills", f"{_MOD}.skill_module.services:SERVICES"),
    ("builtin.job", f"{_MOD}.job_module.services:SERVICES"),
)


# Per-plugin tuples the builtin manifests name (``narranexus.platform.module_system.contributions:PLUGIN_<ID>``).
PLUGIN_AWARENESS = contributions_for("builtin.awareness")
PLUGIN_BASIC_INFO = contributions_for("builtin.basic_info")
PLUGIN_CHANNELS_DISCORD = contributions_for("builtin.channels.discord")
PLUGIN_CHANNELS_LARK = contributions_for("builtin.channels.lark")
PLUGIN_CHANNELS_NARRAMESSENGER = contributions_for("builtin.channels.narramessenger")
PLUGIN_CHANNELS_SLACK = contributions_for("builtin.channels.slack")
PLUGIN_CHANNELS_TELEGRAM = contributions_for("builtin.channels.telegram")
PLUGIN_CHANNELS_WECHAT = contributions_for("builtin.channels.wechat")
PLUGIN_CHAT = contributions_for("builtin.chat")
PLUGIN_COMMON_TOOLS = contributions_for("builtin.common_tools")
PLUGIN_GENERAL_MEMORY = contributions_for("builtin.general_memory")
PLUGIN_HOME_ASSISTANT = contributions_for("builtin.home_assistant")
PLUGIN_JOB = contributions_for("builtin.job")
PLUGIN_MESSAGE_BUS = contributions_for("builtin.message_bus")
PLUGIN_NEXUS_PLUGINS_MODULE = contributions_for("builtin.nexus_plugins_module")
PLUGIN_SKILLS = contributions_for("builtin.skills")
PLUGIN_SOCIAL_NETWORK = contributions_for("builtin.social_network")

TRIGGERS_CHANNELS_DISCORD = trigger_contributions_for("builtin.channels.discord")
TRIGGERS_CHANNELS_LARK = trigger_contributions_for("builtin.channels.lark")
TRIGGERS_CHANNELS_NARRAMESSENGER = trigger_contributions_for("builtin.channels.narramessenger")
TRIGGERS_CHANNELS_SLACK = trigger_contributions_for("builtin.channels.slack")
TRIGGERS_CHANNELS_TELEGRAM = trigger_contributions_for("builtin.channels.telegram")
TRIGGERS_CHANNELS_WECHAT = trigger_contributions_for("builtin.channels.wechat")
TRIGGERS_CHAT = trigger_contributions_for("builtin.chat")
TRIGGERS_JOB = trigger_contributions_for("builtin.job")

__all__ = [
    "BY_PLUGIN",
    "CHANNELS_SLOT",
    "CHANNEL_SPECS",
    "CONTRIBUTIONS",
    "DATA_ACCESS_SLOT",
    "DATA_ACCESS_SPECS",
    "HOOK_SPECS",
    "SERVICE_SPECS",
    "MODULES_SLOT",
    "MODULE_SPECS",
    "TRIGGERS_SLOT",
    "TRIGGER_CONTRIBUTIONS",
    "TRIGGER_SPECS",
    "ModuleSpec",
    "channel_trigger_specs",
    "contributions_for",
    "register_all",
    "spec_for",
    "trigger_contributions_for",
]
