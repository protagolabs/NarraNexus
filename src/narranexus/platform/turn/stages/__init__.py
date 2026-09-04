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
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.slots import Slot
from narranexus.platform.turn.stages import act, assemble, commit, compose, ingress, recall, reflect

OWNER = "builtin.turn"
STAGE_CONTRACT = "narranexus.contracts.agent.pipeline:StageStrategy"


def slot_path(stage: Stage) -> str:
    return f"turn.pipeline.{stage.value}"


INGRESS = (Contribution("default", lambda: ingress.DefaultIngress()),)
RECALL = (
    Contribution("default", lambda: recall.NarrativeLlmRecall()),
    Contribution("narrative_fast", lambda: recall.NarrativeFastRecall()),
    Contribution("ephemeral", lambda: recall.EphemeralRecall()),
)
COMPOSE = (Contribution("default", lambda: compose.DefaultCompose()),)
ASSEMBLE = (Contribution("default", lambda: assemble.LayeredPromptAssemble()),)
ACT = (
    Contribution("default", lambda: act.AgentLoopAct()),
    Contribution("silent", lambda: act.SilentAct()),
)
COMMIT = (Contribution("default", lambda: commit.DefaultCommit()),)
REFLECT = (Contribution("default", lambda: reflect.BackgroundReflect()),)

STRATEGIES: dict[Stage, tuple[Contribution, ...]] = {
    Stage.INGRESS: INGRESS,
    Stage.RECALL: RECALL,
    Stage.COMPOSE: COMPOSE,
    Stage.ASSEMBLE: ASSEMBLE,
    Stage.ACT: ACT,
    Stage.COMMIT: COMMIT,
    Stage.REFLECT: REFLECT,
}


def ensure_registered(registries: Registries = KERNEL_REGISTRIES) -> None:
    """Declare the stage slots (if the tree lacks them) and register the default strategies (idempotent)."""
    for stage, contributions in STRATEGIES.items():
        path = slot_path(stage)
        if path not in registries.slots:
            registries.slots.declare(
                Slot(path, "many", STAGE_CONTRACT, OWNER, doc=f"{stage.value.title()} stage strategies; a profile names one."),
                create_namespaces=True,
            )
        registry = registries.registry_for(path)
        for contribution in contributions:
            if contribution.name not in registry:
                registry.register_contribution(contribution, owner=OWNER)


ensure_registered()

__all__ = ["ACT", "ASSEMBLE", "COMMIT", "COMPOSE", "INGRESS", "OWNER", "RECALL", "REFLECT", "STAGE_CONTRACT", "STRATEGIES", "ensure_registered", "slot_path"]
