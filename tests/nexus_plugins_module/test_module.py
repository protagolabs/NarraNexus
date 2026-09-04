"""
@file_name: test_module.py
@author: Bin Liang
@date: 2026-09-03
@description: The module is registered (MODULE_MAP, core MCP list, port 7811, always-load), is a protected builtin manifest, exposes the sixteen tools, and is silent on cloud.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.kernel.plugins.builtins import builtin_manifests
from xyz_agent_context.module import MODULE_MAP
from xyz_agent_context.module._module_impl.loader import ModuleLoader
from xyz_agent_context.module.module_runner import CORE_MCP_MODULES
from xyz_agent_context.module.nexus_plugins_module import NexusPluginsModule
from xyz_agent_context.schema.context_schema import ContextData

TOOLS = {
    "plugin_list", "plugin_search", "plugin_docs", "plugin_scaffold", "plugin_edit", "plugin_validate", "plugin_test", "plugin_register",
    "plugin_activate", "plugin_observe", "plugin_deactivate", "plugin_rollback", "plugin_diff", "plugin_install", "plugin_upgrade", "plugin_publish_hint",
}


def test_registration():
    assert MODULE_MAP["NexusPluginsModule"] is NexusPluginsModule
    assert "NexusPluginsModule" in CORE_MCP_MODULES
    assert "NexusPluginsModule" in ModuleLoader.always_load_modules(MODULE_MAP)
    m = next(m for m in builtin_manifests() if m.id == "builtin.nexus_plugins_module")
    assert m.protected and m.hosts == ("backend", "mcp")


def test_config_tools_and_cloud_silence(env, monkeypatch):
    module = NexusPluginsModule("a1", "u1", None)
    assert module.get_config().module_type == "capability" and module.config.name == "NexusPluginsModule"
    mcp = module.create_mcp_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == TOOLS
    ctx = ContextData(agent_id="a1", user_id="u1", input_content="hi")
    text = asyncio.run(module.contribute_instructions(ctx))
    assert "plugin_scaffold" in text and "approval" in text
    assert asyncio.run(module.mcp_server()).server_name == "nexus_plugins_module"
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert asyncio.run(module.contribute_instructions(ctx)) == ""
    assert asyncio.run(module.mcp_server()) is None


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tools_answer_json_never_raise(env, tool, monkeypatch):
    module = NexusPluginsModule("a1", "u1", None)
    mcp = module.create_mcp_server()
    args = {"agent_id": "a1", "user_id": "u1"}
    extra = {
        "plugin_search": {"query": "x"}, "plugin_docs": {"kinds": ["routes"]}, "plugin_scaffold": {"plugin_id": "builtin.x", "kinds": ["routes"]},
        "plugin_edit": {"plugin_id": "me.x", "path": "a.py", "content": ""}, "plugin_validate": {"plugin_id": "me.x"}, "plugin_test": {"plugin_id": "me.x"},
        "plugin_register": {"plugin_id": "me.x", "report_hash": "h"}, "plugin_activate": {"plugin_id": "me.x"}, "plugin_observe": {"plugin_id": "me.x"},
        "plugin_deactivate": {"plugin_id": "me.x"}, "plugin_rollback": {}, "plugin_diff": {"plugin_id": "me.x"}, "plugin_install": {"source": "acme/x"},
        "plugin_upgrade": {"plugin_id": "me.x"}, "plugin_publish_hint": {"plugin_id": "me.x"}, "plugin_list": {},
    }[tool]
    import json

    monkeypatch.setattr("xyz_agent_context.module.nexus_plugins_module._nexus_plugins_impl.service.agent_workspace_path", lambda a, u: env["workspace"])
    result = asyncio.run(mcp.call_tool(tool, {**args, **extra}))
    content = result[0] if isinstance(result, tuple) else result  # (content, structured) on newer fastmcp
    first = content[0] if isinstance(content, list) else content.content[0]
    payload = first.text
    json.loads(payload)
