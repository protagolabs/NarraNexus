"""
@file_name: test_hook_manager_parallel_import.py
@author:
@date: 2026-08-14
@description: The parallel data_gathering path must actually be executable.

``HookManager(parallel_data_gathering=True)`` is the documented fast path,
but nothing in the tree enables it, so its body was never executed by any
test — and it shipped importing ``ContextDataMerger`` from a module path
that does not exist (the class lives in ``_module_impl.ctx_merger``).
Same latent-``NameError``/``ImportError`` class as the 2026-08-14 lark-cli
outage: a lazily-executed branch referencing something that resolves for
no interpreter. This test drives the branch for real so the import breaks
the suite instead of the first caller who flips the flag.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.hook_manager import HookManager
from xyz_agent_context.schema.context_schema import ContextData


class _EchoModule:
    """Minimal stand-in: returns its (copied) ctx untouched."""

    class config:  # noqa: D106 — mirrors XYZBaseModule.config.name access
        name = "EchoModule"

    async def hook_data_gathering(self, ctx: ContextData) -> ContextData:
        return ctx


@pytest.mark.asyncio
async def test_parallel_data_gathering_path_is_executable():
    mgr = HookManager(parallel_data_gathering=True)
    ctx = ContextData(agent_id="agent_x", user_id=None, input_content="hi")
    result = await mgr.hook_data_gathering([_EchoModule()], ctx)
    assert isinstance(result, ContextData)
    assert result.agent_id == "agent_x"
