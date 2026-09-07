"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.frameworks.nexus_power — the agent-loop framework contribution (driver factory).
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkMeta
from narranexus.kernel.plugins.registry import Contribution


def _factory(**factory_kwargs):
    from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import NexusAgent

    return NexusAgent(**factory_kwargs)


# NexusPower drives the provider API itself (protocol "any") and refuses
# subscription credentials outright (no oauth_source).
META = FrameworkMeta("nexus_power", "NexusPower", protocol="any", runtime_name="NexusPower-beta")
CONTRIBUTION = Contribution("nexus_power", lambda: _factory, meta={"framework": META})

__all__ = ["CONTRIBUTION", "META"]
