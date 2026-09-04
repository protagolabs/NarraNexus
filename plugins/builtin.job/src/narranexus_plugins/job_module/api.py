"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.job` plugin package — what another plugin or a distribution may import.

Everything else under `narranexus_plugins.job_module` is private to the plugin; the
manifest (`narranexus-plugin.json`) names the contribution constants the host
registers.
"""
from __future__ import annotations

from narranexus.platform.module_system.contributions import spec_for as _spec_for

PLUGIN_ID = "builtin.job"
PACKAGE = "narranexus_plugins.job_module"


def module_class() -> type:
    """The plugin's module class (resolved lazily through the module spec table)."""
    return _spec_for(next(s.class_name for s in __import__("narranexus.platform.module_system.contributions", fromlist=["MODULE_SPECS"]).MODULE_SPECS if s.plugin_id == PLUGIN_ID)).load_class()


__all__ = ["PACKAGE", "PLUGIN_ID", "module_class"]
