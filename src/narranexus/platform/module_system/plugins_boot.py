"""
@file_name: plugins_boot.py
@author: Bin Liang
@date: 2026-09-03
@description: Plugin boot for the agent-side processes (mcp module servers, workers supervisor).

These hosts register declarative contributions only: tools, MCP servers,
skills, workers. They never activate user plugin code (activation needs the
backend's context: settings store, service locator, event bus) and never
register plugin tables (the backend owns migrations). The boot marker is per
role, so an mcp crash loop flips safe mode for mcp without touching the
backend's counter.
"""
from __future__ import annotations

from narranexus.hosts.boot import BootReport, Role, boot
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.plugins.compat import host_version
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

_REPORTS: dict[str, BootReport] = {}


def _boot(role: Role) -> BootReport:
    if role in _REPORTS:
        return _REPORTS[role]
    if KERNEL_REGISTRIES.frozen:
        # Already booted in this process (tests, or a second runner call).
        return _REPORTS.setdefault(role, BootReport(role=role))
    report = boot(role, registries=KERNEL_REGISTRIES, cloud=is_cloud_mode(), host_version=host_version())
    report.mark_healthy()  # no separate health probe here: reaching this line is health
    _REPORTS[role] = report
    return report


def boot_mcp_plugins() -> BootReport:
    return _boot("mcp")


def boot_worker_plugins() -> BootReport:
    return _boot("workers")


def boot_channel_plugins() -> BootReport:
    """The standalone channels supervisor (cloud ``--only channels`` layout) hosts
    the same trigger set the workers process does, so it boots the ``workers`` role."""
    return _boot("workers")


__all__ = ["boot_channel_plugins", "boot_mcp_plugins", "boot_worker_plugins"]
