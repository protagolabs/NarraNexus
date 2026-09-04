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


CONTRIBUTION = Contribution("nexus_power", lambda: _factory, meta={"framework": FrameworkMeta("nexus_power", "NexusPower")})

__all__ = ["CONTRIBUTION"]
