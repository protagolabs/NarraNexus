"""
@file_name: test_turn_loaded_for_every_turn_host.py
@author: Bin Liang
@date: 2026-09-04
@description: Every process that runs turns (backend, mcp, workers) gets the pipeline, the seven stage slots, the profiles and the frameworks from its BOOT — there is no lazy registration any more — and an entry nobody registered fails with a message that says whether the platform was booted at all.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import RegistryFrozen, UnknownEntry
from narranexus.contracts.agent.stages import Stage
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.turn.stages import slot_path


@pytest.mark.parametrize("role", ["backend", "mcp", "workers"])
def test_turn_and_frameworks_are_loaded_by_every_turn_running_role(plugin_home, role):
    regs = Registries()
    report = boot(role, registries=regs, cloud=False, host_version="1.15.0")
    assert regs.frozen and not report.builtins.errors
    # the production invariant: nothing registers after boot
    with pytest.raises(RegistryFrozen):
        regs.registry_for("turn.pipeline.recall").register("late", lambda: 1, owner="acme.late")
    assert "builtin.turn" in {e.owner for e in regs.registry_for("turn.pipeline").entries()}
    for stage in Stage:
        assert regs.registry_for(slot_path(stage)).names(), stage
    assert regs.registry_for("turn.profiles").names()
    assert {"nexus_power", "claude_code", "codex_cli"} <= set(regs.registry_for("turn.pipeline.act.framework").names())
    # the NexusPower seats hang under the framework slot and are populated by the same boot
    assert regs.registry_for("turn.pipeline.act.framework.nexus_power.policy").names() == ("disallowed_tools", "workspace_confinement", "shell_confinement")


def test_an_unbooted_process_says_so(plugin_home):
    regs = Registries()
    with pytest.raises(UnknownEntry, match="booted the plugin platform"):
        regs.registry_for("turn.pipeline.recall").get("narrative_llm")
