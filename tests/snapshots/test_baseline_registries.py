"""
@file_name: test_baseline_registries.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the three runtime registries (agent-loop frameworks, provider drivers, memory kinds).

These are the first registries moved onto the kernel ``Registry[T]``; the
snapshot proves their contents and public accessors survive the move.
"""
from __future__ import annotations

from tests.snapshots._approval import approve


def test_runtime_registries_are_unchanged(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    from xyz_agent_context.agent_framework import available_agent_loop_frameworks
    import xyz_agent_context.agent_framework.providers.driver.drivers  # noqa: F401 registers drivers
    from xyz_agent_context.agent_framework.providers.driver.registry import get_driver_class
    import xyz_agent_context.memory.specs  # noqa: F401 registers kinds
    from xyz_agent_context.memory.spec import all_kinds, passive_kinds

    known_driver_types = [
        "custom_anthropic", "custom_openai", "netmind", "netmind_free", "yunwu",
        "openrouter", "claude_oauth", "codex_oauth", "system",
    ]
    approve(
        "registries",
        {
            "agent_loop_frameworks": available_agent_loop_frameworks(),
            "provider_drivers_present": [t for t in known_driver_types if get_driver_class(t) is not None],
            "memory_kinds": sorted(all_kinds()),
            "memory_passive_kinds": sorted(passive_kinds()),
        },
    )
