"""
@file_name: test_step5_policy_skip.py
@author: NetMind.AI
@date: 2026-06-24
@description: T2 — Step 5 after-execution hooks are skipped under a distrust policy.

A static-visitor (distrust) turn must NOT run module after-execution hooks, so the
owner's persistent state (narrative / memory / chat history) is never mutated by an
external visitor. The owner path (OWNER_POLICY) is unaffected.
"""
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_5_execute_hooks import (
    step_5_execute_hooks,
)
from xyz_agent_context.agent_runtime.runtime_policy import (
    OWNER_POLICY,
    STATIC_VISITOR_POLICY,
)
from xyz_agent_context.schema import ProgressMessage


def _ctx(policy):
    return RunContext(
        agent_id="a", user_id="u", input_content="hi",
        working_source="chat", policy=policy,
    )


@pytest.mark.asyncio
async def test_step5_skips_hooks_under_distrust():
    """Distrust policy → hook_after_event_execution is never called, no callbacks."""
    hook_manager = AsyncMock()
    msgs = [m async for m in step_5_execute_hooks(_ctx(STATIC_VISITOR_POLICY), hook_manager)]

    hook_manager.hook_after_event_execution.assert_not_awaited()
    # Only a progress message is yielded; no callback_results object.
    assert all(isinstance(m, ProgressMessage) for m in msgs)


@pytest.mark.asyncio
async def test_step5_default_policy_does_not_short_circuit():
    """OWNER_POLICY must NOT take the skip branch.

    With a minimal ctx, proceeding past the skip branch reaches
    build_after_execution_params, which needs ctx.execution_result and raises —
    that raise is the proof the skip branch was not taken (it would have returned
    cleanly instead).
    """
    hook_manager = AsyncMock()
    with pytest.raises(AttributeError):
        [m async for m in step_5_execute_hooks(_ctx(OWNER_POLICY), hook_manager)]
