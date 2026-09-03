"""
@file_name: events.py
@author: Bin Liang
@date: 2026-09-03
@description: The fourteen stage hooks: ``onWill<Stage>`` / ``onDid<Stage>`` for each of the seven stages.

Declared as ``HookSpec``-shaped tuples (name, params) so the kernel's hook
registry can declare them without this leaf package importing the kernel.
``onWill*`` receives the stage's inputs and may return a replacement (first
non-None wins); ``onDid*`` receives the stage's output value and is
observation-only.
"""
from __future__ import annotations

from typing import Mapping

from narranexus.contracts.agent.stages import Stage


def hook_name(stage: Stage, *, did: bool) -> str:
    return f"onDid{stage.value.capitalize()}" if did else f"onWill{stage.value.capitalize()}"


# name -> (params, firstresult)
STAGE_HOOKS: Mapping[str, tuple[tuple[str, ...], bool]] = {
    **{hook_name(s, did=False): (("stage", "inputs", "agent_id", "run_id"), True) for s in Stage},
    **{hook_name(s, did=True): (("stage", "output", "agent_id", "run_id"), False) for s in Stage},
}

__all__ = ["STAGE_HOOKS", "hook_name"]
