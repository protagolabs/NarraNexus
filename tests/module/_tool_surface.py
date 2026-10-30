"""
@file_name: _tool_surface.py
@author: Bin Liang
@date: 2026-10-30
@description: One walk over every real module's served MCP tool surface.

Three invariant tests need "every tool the module runner would serve": caller
identity is installed (test_mcp_caller_identity), agent_id tools are async
(identity/test_mcp_auth), user_id tools are classified
(test_mcp_caller_user_identity). They used to each re-implement the walk with
their own private-attribute access and their own empty-sweep guard. This is
the shared walk; each test keeps its OWN guard (a floor or set equality),
because those guards do not mean the same thing and must not be merged down
to the weakest one.
"""
from __future__ import annotations

import inspect
from typing import Callable, NamedTuple


class ServedTool(NamedTuple):
    module: str
    name: str
    fn: Callable
    params: tuple[str, ...]


def served_tools(*, agent_id: str = "probe", user_id: str = "probe") -> tuple[list[ServedTool], list[str]]:
    """Build each registered module the way module_runner does
    (``build_instrumented_mcp_server``) and list its tools.

    Returns (tools, failures). A module that cannot be built under probe args
    is reported in ``failures`` (never silently skipped); the caller decides
    whether that fails its invariant.
    """
    from narranexus.platform.module_system import module_registry

    tools: list[ServedTool] = []
    failures: list[str] = []
    for name, cls in sorted(module_registry.items()):
        try:
            server = cls(agent_id=agent_id, user_id=user_id, database_client=None).build_instrumented_mcp_server()
        except Exception as e:  # noqa: BLE001 — reported to the caller
            failures.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if server is None:
            continue
        for tool in server._tool_manager.list_tools():
            fn = getattr(tool, "fn", None)
            if fn is None:
                continue
            try:
                params = tuple(inspect.signature(fn).parameters)
            except (TypeError, ValueError):
                continue
            tools.append(ServedTool(name, tool.name, fn, params))
    return tools, failures
