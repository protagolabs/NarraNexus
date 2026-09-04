"""
@file_name: test_baseline_registries.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the three runtime registries (agent-loop frameworks, provider drivers, memory kinds).

These are the first registries moved onto the kernel ``Registry[T]``; the
snapshot proves their contents and public accessors survive the move. The
provider registry is populated at import time and depends on the deployment
mode (``SystemDriver`` registers only on cloud), so both modes are captured in
fresh interpreters rather than by monkeypatching after the fact.
"""
from __future__ import annotations

import pytest

from tests.snapshots._approval import approve
from tests.snapshots._subprocess import run_probe

_PROBE = """
import json
from narranexus.platform.agent_framework import available_agent_loop_frameworks
import narranexus_plugins.providers  # noqa: F401 registers drivers
from narranexus.platform.agent_framework.providers.driver.registry import DRIVER_REGISTRY
import narranexus_plugins.memory_kinds.specs  # noqa: F401 registers kinds
from narranexus.platform.memory.spec import all_kinds, passive_kinds
from narranexus.platform.agent_framework.llm.helper_sdk import LLM_CLIENT_REGISTRY
print(json.dumps({
    "agent_loop_frameworks": available_agent_loop_frameworks(),
    "llm_clients": list(LLM_CLIENT_REGISTRY.names()),
    "provider_drivers": list(DRIVER_REGISTRY.names()),
    "provider_driver_owners": sorted(set(e.owner for e in DRIVER_REGISTRY.entries())),
    "memory_kinds": sorted(all_kinds()),
    "memory_passive_kinds": sorted(passive_kinds()),
}))
"""


@pytest.mark.parametrize("mode", ["local", "cloud"])
def test_runtime_registries_are_unchanged(mode):
    view = run_probe(_PROBE, env={"NARRANEXUS_DEPLOYMENT_MODE": mode})
    if mode == "cloud":
        assert "system_pool" in view["provider_drivers"], "cloud registers the system pool driver"
    else:
        assert "system_pool" not in view["provider_drivers"], "local must not register the system pool driver"
    approve(f"registries_{mode}", view)
