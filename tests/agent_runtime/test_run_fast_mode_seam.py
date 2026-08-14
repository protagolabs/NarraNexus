"""
@file_name: test_run_fast_mode_seam.py
@date: 2026-08-14
@description: AgentRuntime.run() actually applies fast_mode to the RunContext.

The pure resolver has its own tests; this file crosses the seam they
don't: revert the ``turn_profile = _resolve_turn_profile(...)`` line in
run() and these go red. RunContext is spied at the module attribute run()
resolves it through, and construction aborts immediately after capture —
no DB, no services, no steps run.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.agent_runtime.agent_runtime as agent_runtime_module
from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime


class _CtxCaptured(Exception):
    pass


async def _capture_ctx_kwargs(monkeypatch, **run_kwargs) -> dict:
    captured: dict = {}

    class _SpyCtx:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise _CtxCaptured()

    monkeypatch.setattr(agent_runtime_module, "RunContext", _SpyCtx)

    runtime = AgentRuntime()
    gen = runtime.run(
        agent_id="agent_a", user_id="user_u", input_content="hi", **run_kwargs
    )
    with pytest.raises(BaseException):
        # run() may re-raise or surface the abort as its own error path;
        # either way construction already captured the kwargs.
        async for _ in gen:
            pass
    assert captured, "RunContext was never constructed"
    return captured


@pytest.mark.asyncio
async def test_fast_mode_true_reaches_run_context(monkeypatch):
    captured = await _capture_ctx_kwargs(monkeypatch, fast_mode=True)
    assert captured["turn_profile"] is not None
    assert captured["turn_profile"].name == "chat_fast"


@pytest.mark.asyncio
async def test_default_run_context_has_no_profile(monkeypatch):
    captured = await _capture_ctx_kwargs(monkeypatch)
    assert captured["turn_profile"] is None
