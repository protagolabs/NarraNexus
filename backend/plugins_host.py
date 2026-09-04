"""
@file_name: plugins_host.py
@author: Bin Liang
@date: 2026-09-03
@description: The backend host's consumption of plugin contributions: mounting ``backend.routes`` entries.

Called once from ``backend.main`` after the shell's own routers and before
the SPA fallback, so a plugin route can never shadow a shell route and the
catch-all can never swallow a plugin route. Non-builtin owners are forced
under ``/api/x/<plugin id>``; a spec that claims another prefix is refused
(the plugin is reported, not mounted). ``auth="none"`` routers are recorded
as auth-exempt prefixes — the only way a plugin obtains a public endpoint,
and always an explicit one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from narranexus.contracts.route import RouterSpec, plugin_route_prefix
from narranexus.kernel.plugins.registries import Registries

from backend import auth as _auth

ROUTES_SLOT = "backend.routes"


@dataclass
class MountReport:
    mounted: list[tuple[str, str, str]] = field(default_factory=list)  # (owner, name, prefix)
    refused: list[tuple[str, str, str]] = field(default_factory=list)  # (owner, name, reason)


def _check_prefix(owner: str, spec: RouterSpec) -> str | None:
    if owner.startswith("builtin."):
        return None
    allowed = plugin_route_prefix(owner)
    if spec.prefix == allowed or spec.prefix.startswith(allowed + "/"):
        return None
    return f"prefix {spec.prefix!r} is outside {allowed!r}"


def mount_plugin_routes(app: Any, registries: Registries) -> MountReport:
    """Mount every ``backend.routes`` contribution onto ``app``."""
    report = MountReport()
    registry = registries.registry_for(ROUTES_SLOT)
    for entry in registry.entries():
        try:
            spec = entry.factory()
        except Exception as exc:  # noqa: BLE001 — a broken factory must not take the host down
            report.refused.append((entry.owner, entry.name, f"{type(exc).__name__}: {exc}"))
            logger.warning(f"[plugins] {entry.owner}: route {entry.name!r} factory failed: {exc}")
            continue
        if not isinstance(spec, RouterSpec):
            report.refused.append((entry.owner, entry.name, f"not a RouterSpec: {type(spec).__name__}"))
            continue
        problem = _check_prefix(entry.owner, spec)
        if problem:
            report.refused.append((entry.owner, entry.name, problem))
            logger.warning(f"[plugins] {entry.owner}: route {entry.name!r} refused: {problem}")
            continue
        if spec.auth == "none":
            # Module attribute, not an import-time binding: tests swap the set.
            _auth.PLUGIN_EXEMPT_PREFIXES.add(spec.prefix + "/")
            _auth.PLUGIN_EXEMPT_PREFIXES.add(spec.prefix)
        app.include_router(spec.router, prefix=spec.prefix, tags=list(spec.tags) or [entry.owner])
        report.mounted.append((entry.owner, entry.name, spec.prefix))
    if report.mounted:
        logger.info(f"[plugins] mounted {len(report.mounted)} plugin router(s): {[m[2] for m in report.mounted]}")
    return report


__all__ = ["MountReport", "ROUTES_SLOT", "mount_plugin_routes"]
