"""
@file_name: contributions.py
@author: Bin Liang
@date: 2026-09-04
@description: The builtin modules as ``agent.capabilities.modules`` contributions — the single table the platform derives MODULE_MAP, MCP ports and the always-load list from.

Each entry names the module class lazily (the module package is imported
only when the factory runs) and carries the metadata the platform used to
keep in three hard-coded lists: ``mcp_port`` (channel modules read their
class attribute), ``always_load`` (auto-enrolled for every agent) and
``channel``. ``builtins.py`` names the same objects from one manifest per
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
    package: str  # xyz_agent_context.module.<package>
    class_name: str
    plugin_id: str
    mcp_port: int | None  # None = channel module: read <class>.mcp_port
    always_load: bool = False

    @property
    def channel(self) -> bool:
        return self.plugin_id.startswith("builtin.channels.")

    def load_class(self) -> type:
        # The class lives in the leaf named like its package (chat_module/chat_module.py);
        # several packages deliberately do not re-export it from __init__.
        leaf = importlib.import_module(f"xyz_agent_context.module.{self.package}.{self.package}")
        return getattr(leaf, self.class_name)

    def port(self) -> int:
        if self.mcp_port is not None:
            return self.mcp_port
        port = getattr(self.load_class(), "mcp_port", None)
        if not port:
            raise ValueError(f"{self.class_name}: channel module must define mcp_port")
        return int(port)

    def contribution(self) -> Contribution[type]:
        return Contribution(
            self.class_name,
            self.load_class,
            meta={"plugin_id": self.plugin_id, "mcp_port": self.mcp_port, "always_load": self.always_load, "channel": self.channel},
        )


MODULE_SPECS: tuple[ModuleSpec, ...] = (
    ModuleSpec("awareness_module", "AwarenessModule", "builtin.awareness", 7801, False),
    ModuleSpec("basic_info_module", "BasicInfoModule", "builtin.basic_info", 7808, False),
    ModuleSpec("chat_module", "ChatModule", "builtin.chat", 7804, False),
    ModuleSpec("social_network_module", "SocialNetworkModule", "builtin.social_network", 7802, False),
    ModuleSpec("job_module", "JobModule", "builtin.job", 7803, False),
    ModuleSpec("skill_module", "SkillModule", "builtin.skills", 7806, True),
    ModuleSpec("message_bus_module", "MessageBusModule", "builtin.message_bus", 7820, False),
    ModuleSpec("common_tools_module", "CommonToolsModule", "builtin.common_tools", 7807, True),
    ModuleSpec("general_memory_module", "GeneralMemoryModule", "builtin.general_memory", 7809, True),
    ModuleSpec("home_assistant_module", "HomeAssistantModule", "builtin.home_assistant", 7810, False),
    ModuleSpec("nexus_plugins_module", "NexusPluginsModule", "builtin.nexus_plugins_module", 7811, True),
    ModuleSpec("lark_module", "LarkModule", "builtin.channels.lark", None, False),
    ModuleSpec("slack_module", "SlackModule", "builtin.channels.slack", None, False),
    ModuleSpec("telegram_module", "TelegramModule", "builtin.channels.telegram", None, False),
    ModuleSpec("wechat_module", "WeChatModule", "builtin.channels.wechat", None, False),
    ModuleSpec("narramessenger_module", "NarramessengerModule", "builtin.channels.narramessenger", None, False),
    ModuleSpec("discord_module", "DiscordModule", "builtin.channels.discord", None, False),
)

BY_PLUGIN: dict[str, tuple[ModuleSpec, ...]] = {}
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
_MOD = "xyz_agent_context.module"

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


def register_all(registries: Any = None) -> None:
    """Import-time registration into the process registries (idempotent; the manifests name the same objects)."""
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    regs = registries or KERNEL_REGISTRIES
    registry = regs.registry_for(MODULES_SLOT)
    for spec in MODULE_SPECS:
        if spec.class_name not in registry:
            registry.register_contribution(CONTRIBUTIONS[spec.class_name], owner=spec.plugin_id)
    triggers = regs.registry_for(TRIGGERS_SLOT)
    for plugin_id, spec in TRIGGER_SPECS:
        if spec.name not in triggers:
            triggers.register_contribution(TRIGGER_CONTRIBUTIONS[spec.name], owner=plugin_id)
    channels = regs.registry_for(CHANNELS_SLOT)
    for plugin_id, ref in CHANNEL_SPECS:
        for contribution in _resolve_symbol(ref):
            if contribution.name not in channels:
                channels.register_contribution(contribution, owner=plugin_id)
            descriptor = contribution.factory()
            if descriptor.has_inbound:
                from xyz_agent_context.schema.hook_schema import WorkingSource

                WorkingSource.register(descriptor.name)
    # Dual-write phase (batch 4b): bespoke credential managers mirror every write into channel_credentials.
    from xyz_agent_context.channel.credential_mirror import install_manager_mirrors

    install_manager_mirrors(regs)
    data_access = regs.registry_for(DATA_ACCESS_SLOT)
    for plugin_id, ref in DATA_ACCESS_SPECS:
        for contribution in _resolve_symbol(ref):
            if contribution.name not in data_access:
                data_access.register_contribution(contribution, owner=plugin_id)
    for plugin_id, ref in HOOK_SPECS:
        for impl in _resolve_symbol(ref):
            regs.hooks.add(impl.hook, impl.fn, owner=plugin_id, tryfirst=impl.tryfirst, trylast=impl.trylast, wrapper=impl.wrapper)
    for plugin_id, ref in SERVICE_SPECS:
        for service_ref, impl in _resolve_symbol(ref):
            if regs.services.try_require(service_ref) is None:
                regs.services.expose(service_ref, impl, owner=plugin_id)


# Per-plugin tuples the builtin manifests name (``xyz_agent_context.module.contributions:PLUGIN_<ID>``).
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
