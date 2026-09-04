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


def register_all(registries: Any = None) -> None:
    """Import-time registration into the process registries (idempotent; the manifests name the same objects)."""
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    regs = registries or KERNEL_REGISTRIES
    registry = regs.registry_for(MODULES_SLOT)
    for spec in MODULE_SPECS:
        if spec.class_name not in registry:
            registry.register_contribution(CONTRIBUTIONS[spec.class_name], owner=spec.plugin_id)


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

__all__ = ["BY_PLUGIN", "CONTRIBUTIONS", "MODULES_SLOT", "MODULE_SPECS", "ModuleSpec", "contributions_for", "register_all", "spec_for"]
