"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: Default stage strategies (the moved ``agent_runtime.run()`` blocks) registered into the ``turn.pipeline.<stage>`` registries.

Import-time registration mirrors the frameworks/providers: the module
constants are ``Contribution``s the ``builtin.turn`` manifest names, so the
loader's manifest-driven registration is an idempotent no-op. The stage
slots are declared here (owner ``builtin.turn``) when the kernel tree does
not have them yet — the manifest ``declares`` the same paths.
"""
from __future__ import annotations

from narranexus.contracts.agent.stages import Stage
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries
from narranexus.kernel.plugins.slots import Slot


OWNER = "builtin.turn"
STAGE_CONTRACT = "narranexus.contracts.agent.pipeline:StageStrategy"


def slot_path(stage: Stage) -> str:
    return f"turn.pipeline.{stage.value}"



def declare_stage_slots(registries: Registries = KERNEL_REGISTRIES) -> None:
    """The seven stage slots are kernel-declared (slots.py) since batch 6; this keeps declaring any
    missing one for a hand-built slot tree (idempotent)."""
    for stage in Stage:
        path = slot_path(stage)
        if path not in registries.slots:
            registries.slots.declare(
                Slot(path, "many", STAGE_CONTRACT, OWNER, doc=f"{stage.value.title()} stage strategies; a profile names one."),
                create_namespaces=True,
            )


def ensure_registered(registries: Registries = KERNEL_REGISTRIES) -> None:
    """Declare the stage slots and make sure the builtin default strategies are
    registered (idempotent). The defaults are the ``builtin.turn`` plugin under
    plugins/ (batch 6b): the kernel resolves the manifest's contributions for
    every stage slot that is still empty — the platform never imports the plugin."""
    from narranexus.kernel.plugins.builtins import register_builtin_provides

    declare_stage_slots(registries)
    for stage in Stage:
        path = slot_path(stage)
        if not registries.registry_for(path).names():
            register_builtin_provides(path, registries)


__all__ = ["OWNER", "STAGE_CONTRACT", "declare_stage_slots", "ensure_registered", "slot_path"]
