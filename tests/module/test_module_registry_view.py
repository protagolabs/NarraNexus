"""
@file_name: test_module_registry_view.py
@author: Bin Liang
@date: 2026-09-04
@description: MODULE_MAP is a live view of the agent.capabilities.modules registry; the module facade resolves classes lazily; derived tables follow it.
"""
from __future__ import annotations

import importlib

import pytest

from narranexus.kernel.plugins.builtins import builtin_manifests
from narranexus.kernel.plugins.registries import Registries
from xyz_agent_context.module import MODULE_MAP
from xyz_agent_context.module._module_map import ModuleMapView
from xyz_agent_context.module.contributions import MODULES_SLOT, MODULE_SPECS, register_all


def test_module_map_lists_every_builtin_module_and_meta():
    names = set(MODULE_MAP)
    assert {s.class_name for s in MODULE_SPECS} == names and "ChatModule" in MODULE_MAP
    assert MODULE_MAP.meta("SkillModule")["always_load"] is True
    assert MODULE_MAP.meta("LarkModule")["channel"] is True and MODULE_MAP.owner_of("LarkModule") == "builtin.channels.lark"
    assert MODULE_MAP["ChatModule"].__name__ == "ChatModule"


def test_facade_resolves_classes_lazily():
    mod = importlib.import_module("xyz_agent_context.module")
    assert mod.ChatModule is MODULE_MAP["ChatModule"]
    with pytest.raises(AttributeError):
        _ = mod.NoSuchModule


def test_every_module_has_a_builtin_manifest_and_view_drops_removed_owner():
    ids = {m.id for m in builtin_manifests()}
    for spec in MODULE_SPECS:
        assert spec.plugin_id in ids, spec.plugin_id
    regs = Registries()
    register_all(regs)
    view = ModuleMapView(MODULES_SLOT, regs)
    assert "DiscordModule" in view
    regs.remove_owner("builtin.channels.discord")
    assert "DiscordModule" not in view and "ChatModule" in view


def test_derived_tables_follow_the_view():
    from xyz_agent_context.module._module_impl.loader import ModuleLoader
    from xyz_agent_context.module.module_runner import CORE_MCP_MODULES, all_mcp_modules

    assert set(CORE_MCP_MODULES) == {s.class_name for s in MODULE_SPECS if not s.channel}
    assert set(ModuleLoader.CORE_ALWAYS_LOAD) == {"SkillModule", "CommonToolsModule", "GeneralMemoryModule", "NexusPluginsModule"}
    assert "LarkModule" in all_mcp_modules() and "ChatModule" in all_mcp_modules()
