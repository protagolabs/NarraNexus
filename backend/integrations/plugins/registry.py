"""
@file_name: registry.py
@author: NarraNexus
@date: 2026-08-28
@description: The installable framework plugins as a lookup table keyed by plugin id (== framework name).

Since batch 1 of the plugin platform this table is DERIVED: every agent-loop
framework contribution whose ``FrameworkMeta.install`` is set (today Claude
Code and Codex CLI) becomes one ``PluginSpec``. The pins therefore live in one
place — ``xyz_agent_context.agent_framework`` next to the driver factories —
and the invariant "installer pin == locked version" is guarded by
``tests/backend/integrations/plugins/test_registry.py::test_pip_pins_match_uv_lock``.
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkMeta
from xyz_agent_context.agent_framework.loop.driver import FRAMEWORK_REGISTRY

from .spec import InstallComponent, PluginSpec


def _spec_from_meta(meta: FrameworkMeta) -> PluginSpec:
    assert meta.install is not None
    return PluginSpec(
        id=meta.name,
        display_name=meta.display_name,
        framework_name=meta.name,
        components=tuple(
            InstallComponent(kind=c.kind, requirement=c.requirement) for c in meta.install.components
        ),
        probe_package=meta.install.probe_package,
        user_version_source=meta.install.user_version_source,
        size_hint=meta.install.size_hint,
    )


def build_plugin_specs() -> dict[str, PluginSpec]:
    """Installable plugins = registered frameworks that declare an install recipe."""
    import xyz_agent_context.agent_framework  # noqa: F401 — registers the builtin frameworks

    specs: dict[str, PluginSpec] = {}
    for entry in FRAMEWORK_REGISTRY.entries():
        meta = entry.meta.get("framework")
        if isinstance(meta, FrameworkMeta) and meta.install is not None:
            specs[entry.name] = _spec_from_meta(meta)
    return specs


PLUGIN_SPECS: dict[str, PluginSpec] = build_plugin_specs()

__all__ = ["PLUGIN_SPECS", "build_plugin_specs"]
