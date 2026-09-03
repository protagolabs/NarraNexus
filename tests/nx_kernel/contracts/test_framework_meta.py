"""
@file_name: test_framework_meta.py
@author: Bin Liang
@date: 2026-09-03
@description: Framework install metadata rides the framework contributions and the backend installer table derives from it.
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta


def test_builtin_frameworks_carry_framework_meta():
    import xyz_agent_context.agent_framework  # noqa: F401
    from xyz_agent_context.agent_framework.loop.driver import FRAMEWORK_REGISTRY

    metas = {e.name: e.meta["framework"] for e in FRAMEWORK_REGISTRY.entries() if "framework" in e.meta}
    assert set(metas) >= {"nexus_power", "claude_code", "codex_cli"}
    assert all(isinstance(m, FrameworkMeta) for m in metas.values())
    assert metas["nexus_power"].install is None
    assert isinstance(metas["claude_code"].install, FrameworkInstall)
    assert [c.kind for c in metas["claude_code"].install.components] == ["pip", "npm"]
    assert metas["codex_cli"].install.probe_package == "openai_codex"


def test_backend_installer_table_is_derived_from_the_registry():
    from backend.integrations.plugins.registry import PLUGIN_SPECS, build_plugin_specs

    assert set(PLUGIN_SPECS) == {"claude_code", "codex_cli"}
    assert build_plugin_specs() == PLUGIN_SPECS
    assert PLUGIN_SPECS["claude_code"].components[1].requirement.startswith("@anthropic-ai/claude-code@")
