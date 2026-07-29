"""
@file_name: loop.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusAgentLoop — the phase machine. It decides "what
happens next" and nothing else; every fork is a strategy call, every
capability is a channel call. The extension roadmap requires zero edits
here — that is the design's core promise (and why the ≤500-line review
gate is sustainable).

Phases per step: PROJECT (compaction check + context projection) →
MODEL_STREAM (typed events out of the model client; argument-field
streaming for declared tools) → DISPATCH (policy-checked execution) →
DRAIN_STEERING (v1: always empty) → STOP_CHECK (v1: no actions = stop).

Hard guarantees:
  - cancellation lands at safe boundaries and NEVER splits a
    tool_use/result pair (synthesis closes open calls);
  - every termination path emits exactly one ``turn_done`` with real
    cumulative usage (the billing chain's sole source);
  - a ``CONTEXT_OVERFLOW`` classification triggers compaction + step
    retry (progress-guarded), so long turns never die on the context
    wall;
  - iron rule #14: no iteration/duration ceiling exists in this file.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from loguru import logger

from xyz_agent_context.agent_framework.nexus_loop.contracts.errors import (
    ErrorType,
    LoopError,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.events import (
    TYPE_ERROR,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    EndReason,
    LoopEvent,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    ModelRequest,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolCall
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.harness.hooks import (
    HookEvent,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.modeling.arg_stream import (
    StreamingArgExtractor,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.modeling.prompt_cache import (
    plan_cache,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.session.turn_ledger import (
    TurnLedger,
)


class NexusAgentLoop:
    """One instance runs one turn (no cross-turn state: stateless worker)."""

    def __init__(self, assembly: Any, ledger: TurnLedger) -> None:
        self._a = assembly
        self._ledger = ledger
        self._closed = False

    async def run_turn(self) -> AsyncIterator[LoopEvent]:
        a, ledger = self._a, self._ledger
        try:
            while True:
                # ---- boundary: cancellation --------------------------------
                if a.cancel.requested():
                    async for ev in self._interrupt("cancelled by user"):
                        yield ev
                    return

                # ---- PROJECT ----------------------------------------------
                if a.compaction.should_compact(ledger, a.model.profile):
                    async for ev in self._compact("proactive"):
                        yield ev
                request = self._build_request()

                # ---- MODEL_STREAM (with overflow-compaction retry) --------
                step_calls: list[ToolCall] = []
                attempt = 0
                while True:
                    step_calls, error = [], None
                    try:
                        async for ev in self._stream_step(request, step_calls):
                            yield ev
                    except Exception as exc:  # noqa: BLE001 - classified below
                        error = a.errors.classify(exc)
                    if error is None:
                        break
                    if error.error_type is ErrorType.CONTEXT_OVERFLOW:
                        compacted = False
                        async for ev in self._compact("reactive"):
                            compacted = True
                            yield ev
                        if compacted:
                            request = self._build_request()
                            continue  # retry the step with a smaller context
                    attempt += 1
                    if error.retryable and await a.retry.should_retry(error, attempt):
                        continue
                    async for ev in self._fail(error):
                        yield ev
                    return

                # ---- DISPATCH ---------------------------------------------
                for call in step_calls:
                    if a.cancel.requested():
                        async for ev in self._interrupt("cancelled by user"):
                            yield ev
                        return
                    outcome = await a.hooks.fire(
                        HookEvent.PRE_TOOL_USE, {"call": call}
                    )
                    if not outcome.allowed:
                        result = call_denied_by_hook(call, outcome.notes)
                    else:
                        result = await a.tools.execute(call)
                    await a.hooks.fire(
                        HookEvent.POST_TOOL_USE, {"call": call, "result": result}
                    )
                    for ev in ledger.record_tool_result(call.id, result):
                        yield await self._log(ev)

                # ---- DRAIN_STEERING ---------------------------------------
                injected = await a.steering.drain()
                if injected:
                    ledger.record_steering(injected)
                    continue

                # ---- STOP_CHECK -------------------------------------------
                if await a.stop.should_stop(step_calls, ledger):
                    await a.hooks.fire(HookEvent.STOP, {"steps": ledger.num_steps()})
                    yield await self._close(EndReason.NO_MORE_ACTIONS)
                    return
        finally:
            if not self._closed:
                # Belt-and-braces: no path may leave the turn unclosed
                # (turn_done is the billing chain's sole source).
                logger.warning("loop exited without closure; emitting turn_done")
                await self._log(self._ledger.close_turn(
                    EndReason.ERROR, model=self._a.params.model
                ))

    # ------------------------------------------------------------------
    # Phase helpers (no business logic beyond orchestration)
    # ------------------------------------------------------------------

    def _build_request(self) -> ModelRequest:
        a = self._a
        messages = a.projector.project(self._ledger, a.model.profile)
        tools = [spec.as_openai_tool() for spec in a.tools.visible_tools()]
        return ModelRequest(
            messages=messages,
            tools=tools,
            params=a.params,
            cache_plan=plan_cache(messages, a.model.profile),
        )

    async def _stream_step(
        self, request: ModelRequest, step_calls: list[ToolCall]
    ) -> AsyncIterator[LoopEvent]:
        a, ledger = self._a, self._ledger
        extractors: dict[int, StreamingArgExtractor] = {}
        async for model_event in a.model.stream_step(request):
            kind = model_event.kind
            if kind == "tool_use_start" and a.include_arg_deltas:
                index = int(model_event.payload.get("call_index", 0))
                spec = a.tools.spec_for(str(model_event.payload.get("tool_name", "")))
                fields = spec.annotations.streamable_fields if spec else ()
                if fields:
                    extractors[index] = StreamingArgExtractor(index, tuple(fields))
                continue
            if kind == "arg_delta":
                index = int(model_event.payload.get("call_index", 0))
                extractor = extractors.get(index)
                if extractor is not None:
                    for delta in extractor.feed(str(model_event.payload.get("text", ""))):
                        yield await self._log(
                            ledger.record_arg_field_delta(
                                delta.call_index, delta.field_path, delta.text
                            )
                        )
                continue
            events = ledger.record_model_event(model_event)
            if kind == "tool_use":
                payload = model_event.payload
                step_calls.append(
                    ToolCall(
                        id=str(payload["call_id"]),
                        name=str(payload["tool_name"]),
                        args=dict(payload.get("args") or {}),
                    )
                )
                index = int(model_event.content_index)
                extractor = extractors.get(index)
                if extractor is not None:
                    for delta in extractor.finalize(dict(payload.get("args") or {})):
                        yield await self._log(
                            ledger.record_arg_field_delta(
                                delta.call_index, delta.field_path, delta.text
                            )
                        )
            for ev in events:
                yield await self._log(ev)

    async def _compact(self, mode: str) -> AsyncIterator[LoopEvent]:
        a, ledger = self._a, self._ledger
        await a.hooks.fire(HookEvent.PRE_COMPACT, {"mode": mode})
        entries = await a.compaction.compact(ledger, a.model.profile)
        if entries:
            logger.info(f"compaction ({mode}): {len(entries)} replacement entries")
            for ev in ledger.apply_compaction(entries):
                yield await self._log(ev)
        await a.hooks.fire(HookEvent.POST_COMPACT, {"count": len(entries)})

    async def _interrupt(self, reason: str) -> AsyncIterator[LoopEvent]:
        for ev in self._ledger.synthesize_interrupted_results(reason):
            yield await self._log(ev)
        yield await self._close(EndReason.INTERRUPTED)

    async def _fail(self, error: LoopError) -> AsyncIterator[LoopEvent]:
        for ev in self._ledger.synthesize_interrupted_results(
            f"step failed: {error.error_type.value}"
        ):
            yield await self._log(ev)
        yield await self._log(
            LoopEvent(
                track="ui",
                seq=-1,  # transient; not a ledger row (the ledger owns seqs)
                type=TYPE_ERROR,
                payload={
                    "error_type": error.error_type.value,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            )
        )
        yield await self._close(EndReason.ERROR)

    async def _close(self, reason: EndReason) -> LoopEvent:
        self._closed = True
        return await self._log(
            self._ledger.close_turn(reason, model=self._a.params.model)
        )

    async def _log(self, event: LoopEvent) -> LoopEvent:
        if event.type in (TYPE_TEXT_DELTA, TYPE_THINKING_DELTA):
            event = self._a.expression.tag_text_event(event)
        await self._a.log.append(event)  # logging is pass-through, not a fork
        return event


def call_denied_by_hook(call: ToolCall, notes: tuple[str, ...]):
    from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import ToolResult

    return ToolResult(
        call_id=call.id,
        ok=False,
        error="denied by hook: " + ("; ".join(notes) or "vetoed"),
    )
