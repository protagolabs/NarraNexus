"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.message_bus`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import module_contribution

PLUGIN_ID = "builtin.message_bus"
MODULES = (module_contribution("narranexus_plugins.message_bus_module.message_bus_module:MessageBusModule", PLUGIN_ID, channel=False),)

__all__ = ["MODULES", "PLUGIN_ID"]
