"""
@file_name: test_baseline_workers.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the worker supervisor's canonical worker set and startup order.
"""
from __future__ import annotations

from tests.snapshots._approval import approve


def test_worker_specs_are_unchanged():
    """The effective builtin worker set: platform workers plus builtin trigger workers ("jobs" since 3c.3)."""
    from narranexus.kernel.plugins.registries import Registries
    from xyz_agent_context.module.contributions import register_all
    from xyz_agent_context.module.run_worker_supervisor import ALL_WORKERS, build_specs

    regs = Registries()
    register_all(regs)
    approve("workers", {"order": list(ALL_WORKERS), "specs": sorted(s.name for s in build_specs(registries=regs))})
