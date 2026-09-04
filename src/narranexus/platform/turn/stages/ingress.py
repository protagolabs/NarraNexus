"""
@file_name: ingress.py
@author: Bin Liang
@date: 2026-09-04
@description: Ingress stage default strategy: step_0 (config, Event, Session) + the owner's LLM configuration for this turn.

Moved verbatim from ``AgentRuntime.run()``. A resolution failure persists
the error marker on the event, yields the ``ErrorMessage`` and marks the
services ``aborted`` so the pipeline stops — exactly the legacy early
``return``.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from loguru import logger

from narranexus.contracts.agent.stages import Stage
from narranexus.platform.turn.inputs import StageInputs


class DefaultIngress:
    stage = Stage.INGRESS

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        # Steps are looked up on the agent_runtime module (not imported here) so
        # the existing test seams that monkeypatch ``agent_runtime.step_*`` keep working.
        from xyz_agent_context.agent_runtime import agent_runtime as ar

        ctx, s = inputs.ctx, inputs.services
        async for msg in ar.step_0_initialize(ctx, s.db_client, s.event_service, s.session_service):
            yield msg

        from xyz_agent_context.agent_framework.api_config import (
            LLMResolverError,
            get_agent_owner_runtime_llm_configs,
            set_user_config,
        )

        try:
            owner_configs = await get_agent_owner_runtime_llm_configs(ctx.agent_id)
            set_user_config(
                owner_configs.claude,
                owner_configs.openai,
                owner_configs.codex,
                owner_configs.anthropic_helper,
                owner_configs.cli_helper,
            )
        except LLMResolverError as e:
            logger.warning(f"LLM config resolution failed for agent {ctx.agent_id}: {type(e).__name__}: {e}")
            error_marker = f"[ERROR:{type(e).__name__}] {e}"
            try:
                if ctx.event and ctx.event.id:
                    await s.event_service.update_event_in_db(event_id=ctx.event.id, final_output=error_marker)
                    ctx.event.final_output = error_marker
            except Exception as persist_err:  # noqa: BLE001 — best-effort
                logger.warning(f"Failed to persist error marker on event {getattr(ctx.event, 'id', '?')}: {persist_err}")
            from xyz_agent_context.schema import ErrorMessage

            s.aborted = True
            yield ErrorMessage(error_message=str(e), error_type=type(e).__name__)


__all__ = ["DefaultIngress"]
