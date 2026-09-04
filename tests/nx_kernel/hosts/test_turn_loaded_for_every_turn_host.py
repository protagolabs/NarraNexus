"""
@file_name: test_turn_loaded_for_every_turn_host.py
@author: Bin Liang
@date: 2026-09-04
@description: Regression for the workers-process RegistryFrozen ("turn.pipeline: cannot register 'builtin.turn' after freeze()"): every process that runs turns (backend, mcp, workers) must get the pipeline, the seven stage slots, the profiles and the frameworks from its boot, so the lazy ensure_* seams find them populated after freeze; and a frozen, empty slot fails with a clear message instead of RegistryFrozen.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.agent.stages import Stage
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.builtins import register_builtin_provides
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.module_system.contributions import register_all
from narranexus.platform.turn.stages import slot_path


@pytest.mark.parametrize("role", ["backend", "mcp", "workers"])
def test_turn_and_frameworks_are_loaded_by_every_turn_running_role(plugin_home, role):
    regs = Registries()
    register_all(regs)
    report = boot(role, registries=regs, cloud=False, host_version="1.15.0")
    assert regs.frozen and not report.builtins.errors
    assert "builtin.turn" in {e.owner for e in regs.registry_for("turn.pipeline").entries()}
    for stage in Stage:
        assert regs.registry_for(slot_path(stage)).names(), stage
    assert regs.registry_for("turn.profiles").names()
    assert {"nexus_power", "claude_code", "codex_cli"} <= set(regs.registry_for("turn.pipeline.act.framework").names())
    # the lazy seams are no-ops now (nothing to register after freeze)
    from narranexus.platform.turn.stages import ensure_registered

    ensure_registered(regs)  # every slot populated: nothing to register, nothing raised


def test_frozen_empty_slot_fails_loudly(plugin_home):
    regs = Registries()
    regs.freeze()
    with pytest.raises(RuntimeError, match="turn.pipeline.recall: no contribution loaded at boot"):
        register_builtin_provides("turn.pipeline.recall", regs)
