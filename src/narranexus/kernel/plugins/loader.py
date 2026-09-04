"""
@file_name: loader.py
@author: Bin Liang
@date: 2026-09-03
@description: Discover manifests and load their contributions into the registries, per host role.

Batch 0 scope: builtin plugins only. ``discover`` returns the builtin manifests
and, on the cloud deployment, nothing else — ever (D1: the cloud runs zero
user plugins; the user registry path is ignored there by construction, not by
configuration). User-plugin discovery from ``registry.json`` arrives with the
plugin factory in batch 2 and plugs into the same ``load``.

``load`` is deliberately small: for each manifest whose ``hosts`` include the
current role, resolve every ``provides`` symbol (``module.path:attr``) and
register the ``Contribution``(s) it names into the registry for that slot.
Import happens here and only here, so a plugin's import cost is paid once and
attributed to it in the ``LoadReport``. A builtin that fails to import raises —
a broken builtin must stop the process (today's behavior) — while a user plugin
is isolated (batch 2 wires the crash counter).
"""
from __future__ import annotations

import importlib
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from narranexus.contracts import ManifestError, PluginError
from narranexus.kernel.plugins.manifest import Host, Manifest
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution


@dataclass(frozen=True)
class PluginLoad:
    plugin_id: str
    version: str
    slots: tuple[str, ...]
    entries: int
    duration_ms: float
    error: str | None = None


@dataclass
class LoadReport:
    role: Host
    loaded: list[PluginLoad] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # not for this host

    @property
    def errors(self) -> list[PluginLoad]:
        return [p for p in self.loaded if p.error]


def discover(*, cloud: bool, user_registry_path: Path | None = None) -> list[Manifest]:
    """Builtin manifests, plus (never on cloud) user plugins.

    Batch 0 knows only builtins; ``user_registry_path`` is accepted so the
    call shape is final, and is ignored on cloud by construction.
    """
    from narranexus.kernel.plugins.builtins import builtin_manifests

    manifests = list(builtin_manifests())
    if cloud:
        if user_registry_path is not None:
            logger.info("[plugins] cloud deployment: user plugin registry ignored (fail-closed)")
        return manifests
    if user_registry_path is not None:
        logger.debug(f"[plugins] user registry {user_registry_path} not consulted in batch 0")
    return manifests


def resolve_symbol(spec: str) -> Any:
    module_path, _, attr = spec.partition(":")
    module = importlib.import_module(module_path)
    try:
        return getattr(module, attr)
    except AttributeError:
        raise PluginError(f"{spec}: module {module_path!r} has no attribute {attr!r}") from None


def _as_contributions(value: Any, spec: str) -> list[Contribution[Any]]:
    if isinstance(value, Contribution):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        items = list(value)
        if all(isinstance(i, Contribution) for i in items):
            return items
    raise PluginError(f"{spec}: expected a Contribution or an iterable of Contributions, got {type(value).__name__}")


def load_order(manifests: Iterable[Manifest]) -> list[Manifest]:
    """Builtins in declaration order, then user plugins by id.

    This is what makes ``Registry.names()`` byte-stable across restarts: the
    outer order is fixed here, the inner order is each manifest's ``provides``.
    Dependency-aware (topological) ordering arrives with plugin dependencies in
    batch 2 and slots in between these two groups.
    """
    items = list(manifests)
    builtins = [m for m in items if m.is_builtin]
    users = sorted((m for m in items if not m.is_builtin), key=lambda m: m.id)
    return builtins + users


def load(registries: Registries, manifests: Iterable[Manifest], *, role: Host) -> LoadReport:
    """Register every contribution of the manifests that target ``role``."""
    report = LoadReport(role=role)
    seen: set[str] = set()
    for manifest in load_order(manifests):
        if manifest.id in seen:
            raise ManifestError(f"duplicate plugin id {manifest.id!r} in load set")
        seen.add(manifest.id)
        if role not in manifest.effective_hosts():
            report.skipped.append(manifest.id)
            continue
        started = time.perf_counter()
        entries = 0
        error: str | None = None
        try:
            # A plugin's own slot declarations must exist before it (or anyone)
            # provides into them; declaring is idempotent per process.
            for slot in manifest.declared_slots():
                if slot.path not in registries.slots:
                    registries.slots.declare(slot, create_namespaces=True)
            for path, value in manifest.provides.items():
                registry = registries.registry_for(path)
                specs = (value,) if isinstance(value, str) else value
                for spec in specs:
                    contributions = _as_contributions(resolve_symbol(spec), spec)
                    if not contributions:
                        logger.debug(f"[plugins] {manifest.id}: {spec} produced no contributions")
                    for contribution in contributions:
                        registry.register_contribution(contribution, owner=manifest.id)
                        entries += 1
        except Exception as exc:  # noqa: BLE001 — classify below
            error = f"{type(exc).__name__}: {exc}"
            if manifest.is_builtin:
                logger.error(f"[plugins] builtin {manifest.id} failed to load: {error}")
                raise
            logger.warning(f"[plugins] {manifest.id} failed to load and was isolated: {error}")
        report.loaded.append(
            PluginLoad(
                plugin_id=manifest.id,
                version=manifest.version,
                slots=tuple(manifest.provides),
                entries=entries,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                error=error,
            )
        )
    return report


__all__ = ["PluginLoad", "LoadReport", "discover", "load", "load_order", "resolve_symbol"]
