"""
@file_name: stages.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.turn — the default strategy per stage, as the contributions the manifest names (turn.pipeline.<stage>).
"""
from __future__ import annotations

from narranexus.contracts.agent.stages import Stage
from narranexus.kernel.plugins.registry import Contribution
from narranexus_plugins.turn import act, assemble, commit, compose, ingress, recall, reflect

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

__all__ = ["ACT", "ASSEMBLE", "COMMIT", "COMPOSE", "INGRESS", "RECALL", "REFLECT", "STRATEGIES"]
