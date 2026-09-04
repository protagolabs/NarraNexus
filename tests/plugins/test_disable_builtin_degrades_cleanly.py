"""
@file_name: test_disable_builtin_degrades_cleanly.py
@author: Bin Liang
@date: 2026-09-04
@description: Disabling any builtin module plugin through registry.json removes its row everywhere (module_registry, MCP ports, always-load) and the rest keeps booting; protected builtins cannot be disabled.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.module_system._module_impl.loader import ModuleLoader
from narranexus.platform.module_system.registry import ModuleRegistry
from narranexus.platform.module_system.contributions import MODULES_SLOT, MODULE_SPECS, register_all

DISABLEABLE = [s for s in MODULE_SPECS if s.plugin_id != "builtin.nexus_plugins_module"]


def _boot_with_override(tmp_path: Path, monkeypatch, plugin_id: str, enabled: bool):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    store.update(lambda reg: reg.builtin_overrides.__setitem__(plugin_id, {"enabled": enabled}))
    regs = Registries()
    register_all(regs)
    report = boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    report.mark_healthy()
    return regs, report


@pytest.mark.parametrize("spec", DISABLEABLE, ids=[s.plugin_id for s in DISABLEABLE])
def test_disable_builtin_degrades_cleanly(spec, tmp_path: Path, monkeypatch):
    regs, report = _boot_with_override(tmp_path, monkeypatch, spec.plugin_id, enabled=False)
    assert report.disabled_builtins == (spec.plugin_id,)
    view = ModuleRegistry(regs)
    assert spec.class_name not in view
    others = {s.class_name for s in MODULE_SPECS if s.plugin_id != spec.plugin_id}
    assert others <= set(view)  # every other module still loads
    if spec.load_class().get_config().always_load:
        assert spec.class_name not in ModuleLoader.always_load_modules(dict(view))
    assert regs.frozen and report.builtins is not None and not report.builtins.errors


def test_protected_builtin_ignores_the_override(tmp_path: Path, monkeypatch):
    regs, report = _boot_with_override(tmp_path, monkeypatch, "builtin.nexus_plugins_module", enabled=False)
    assert report.disabled_builtins == ()
    assert "NexusPluginsModule" in ModuleRegistry(regs)


def test_override_enabled_true_is_a_no_op(tmp_path: Path, monkeypatch):
    regs, report = _boot_with_override(tmp_path, monkeypatch, "builtin.chat", enabled=True)
    assert report.disabled_builtins == () and "ChatModule" in ModuleRegistry(regs)


TRIGGER_OWNERS = [s for s in DISABLEABLE if s.channel or s.plugin_id == "builtin.job"]


@pytest.mark.parametrize("spec", TRIGGER_OWNERS, ids=[s.plugin_id for s in TRIGGER_OWNERS])
def test_disabling_a_builtin_removes_its_ingress_trigger(spec, tmp_path: Path, monkeypatch):
    from narranexus.platform.module_system import run_worker_supervisor as sup
    from narranexus.platform.module_system.channel_trigger_map import TriggerMapView

    regs, report = _boot_with_override(tmp_path, monkeypatch, spec.plugin_id, enabled=False)
    owners = {e.owner for e in regs.registry_for("ingress.triggers").entries()}
    assert spec.plugin_id not in owners and owners  # its trigger is gone, the others remain
    if spec.plugin_id == "builtin.job":
        assert "jobs" not in [s.name for s in sup.build_specs(registries=regs)]
    else:
        assert len(TriggerMapView(regs)) == 5
