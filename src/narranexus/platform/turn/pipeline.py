"""
@file_name: pipeline.py
@author: Bin Liang
@date: 2026-09-04
@description: ``TurnPipeline`` — the one place the seven stages run, in order, through the strategies a profile names, with hooks at every boundary.

For each stage: ``onWill<Stage>`` (hooks may observe the frozen inputs;
returning a value is reserved for a later batch) → the strategy the profile
selects from the ``turn.pipeline.<stage>`` registry → ``onDid<Stage>`` with
the frozen output view. Stage messages stream through to the caller
unchanged. Hooks run with the profile's sync budget and never fail the turn.
``resolve_profile`` maps the legacy flags (fast_mode / silent / an explicit
TurnProfile) onto a builtin profile so the trigger call sites keep working.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable

from loguru import logger

from narranexus.contracts import UnknownEntry
from narranexus.contracts.agent.events import hook_name
from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import STAGES, Stage
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.turn import observe
from narranexus.platform.turn.inputs import StageInputs, TurnServices
from narranexus.platform.turn.profiles import BUILTIN_PROFILES

PROFILES_SLOT = "turn.profiles"

_VIEWS: dict[Stage, Callable[[Any], Any]] = {
    Stage.INGRESS: observe.ingress_view,
    Stage.RECALL: observe.recall_view,
    Stage.COMPOSE: observe.compose_view,
    Stage.ASSEMBLE: observe.assemble_view,
    Stage.ACT: observe.act_view,
    Stage.COMMIT: observe.commit_view,
    Stage.REFLECT: observe.reflect_view,
}


def resolve_profile(
    *,
    fast_mode: bool,
    silent: bool,
    turn_profile: Any,
    working_source: Any,
    registries: Registries | None = None,
    explicit: str | None = None,
) -> PipelineProfile:
    """Profile selection: explicit id > silent > TurnProfile name (voice/fast) > fast_mode > source job > default.

    ``explicit`` is the TURN layer of the binding order (a caller naming a
    registered profile, e.g. a plugin-defined ``research``); it must exist in
    the ``turn.profiles`` registry or the builtin table, else ``UnknownEntry``.
    """
    regs = registries or KERNEL_REGISTRIES
    if explicit:
        return _profile(explicit, regs)
    if silent:
        return _profile("silent", regs)
    if turn_profile is not None:
        name = str(getattr(turn_profile, "name", "") or "")
        if "voice" in name:
            return _profile("voice", regs)
        if getattr(turn_profile, "narrative_strategy", "full") == "bm25_top1":
            return _profile("fast", regs)
    if fast_mode:
        return _profile("fast", regs)
    source = str(getattr(working_source, "value", working_source) or "")
    if source == "job":
        return _profile("job", regs)
    return _profile("default", regs)


def _profile(profile_id: str, regs: Registries) -> PipelineProfile:
    if PROFILES_SLOT in regs.slots:
        registry = regs.registry_for(PROFILES_SLOT)
        if profile_id in registry:
            return registry.get(profile_id)
    try:
        return BUILTIN_PROFILES[profile_id]
    except KeyError:
        raise UnknownEntry(f"pipeline profile {profile_id!r} is not registered") from None


class TurnPipeline:
    """Runs one turn; ``registries`` defaults to the process registries."""

    def __init__(self, registries: Registries | None = None) -> None:
        from narranexus.platform.turn.stages import ensure_registered, slot_path

        from narranexus.platform.turn import ensure_pipeline_registered

        self.registries = registries or KERNEL_REGISTRIES
        ensure_registered(self.registries)
        ensure_pipeline_registered(self.registries)
        self._slot_path = slot_path

    def strategy_for(self, stage: Stage, profile: PipelineProfile) -> Any:
        name = profile.strategy_for(stage)
        registry = self.registries.registry_for(self._slot_path(stage))
        return registry.get(name)  # UnknownEntry: a profile naming a strategy nobody provides fails loud

    async def _hook(self, stage: Stage, *, did: bool, ctx: Any, profile: PipelineProfile) -> None:
        name = hook_name(stage, did=did)
        hooks = self.registries.hooks
        if name not in hooks or not hooks.caller(name).owners():
            return
        view = _VIEWS[stage](ctx)
        payload = {"stage": stage.value, ("output" if did else "inputs"): view, "agent_id": ctx.agent_id, "run_id": getattr(ctx, "run_id", "")}
        try:
            await asyncio.wait_for(hooks.caller(name).call(**payload), timeout=profile.budgets.sync_hook_total_s)
        except asyncio.TimeoutError:
            logger.warning(f"[turn] {name} hooks exceeded {profile.budgets.sync_hook_total_s}s and were abandoned")
        except Exception as exc:  # noqa: BLE001 — hooks never fail the turn
            logger.warning(f"[turn] {name} hooks raised {exc!r}")

    async def run(self, ctx: Any, profile: PipelineProfile, services: TurnServices, *, silent: bool = False) -> AsyncIterator[Any]:
        t = services.timings
        t.setdefault("run_start", time.monotonic())
        t["setup_start"] = time.monotonic()
        for stage in STAGES:
            if stage is Stage.ACT:
                t["loop_start"] = time.monotonic()
            await self._hook(stage, did=False, ctx=ctx, profile=profile)
            strategy = self.strategy_for(stage, profile)
            async for msg in strategy.run(StageInputs(ctx=ctx, services=services, profile=profile, silent=silent)):
                yield msg
            if stage is Stage.ACT:
                t["loop_end"] = time.monotonic()
            await self._hook(stage, did=True, ctx=ctx, profile=profile)
            if services.aborted:
                return


PIPELINE_CONTRIBUTION = Contribution("builtin.turn", lambda: TurnPipeline)

__all__ = ["PIPELINE_CONTRIBUTION", "PROFILES_SLOT", "TurnPipeline", "resolve_profile"]
