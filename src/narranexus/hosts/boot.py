"""
@file_name: boot.py
@author: Bin Liang
@date: 2026-09-03
@description: Staged plugin boot for one host process (spec §9.2): builtins fail-fast, user plugins isolated, then freeze.

    stage1  builtins (declaration order)         a failure raises — a broken builtin must not boot
    stage2  user plugins (dependency order)      each isolated; total deadline; failures recorded on registry.json
            per plugin: synthetic package + deps finder → declarative contributions → tables registered
                        → activator learns its events → onStartup fired by the host when ready
    registries.freeze()

The boot marker is entered before stage 2 and exited by the host once it is
healthy (``BootReport.mark_healthy``). If a marker was left behind twice in
a row, ``registry.json`` flips to safe mode and this boot loads builtins
only. Everything the boot decides is returned in ``BootReport`` so the
factory page can show it; nothing is hidden in logs alone.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from loguru import logger

from narranexus.kernel.plugins.activation import Activator
from narranexus.kernel.plugins.importer import install_synthetic_package, plugin_finder
from narranexus.kernel.plugins.lifecycle import BootMarker, RegistryError, RegistryStore
from narranexus.kernel.plugins.distribution import DistributionResolution
from narranexus.kernel.plugins.loader import Discovery, LoadReport, discover, load
from narranexus.kernel.plugins.manifest import Manifest
from narranexus.kernel.plugins.paths import deps_dir, registry_path
from narranexus.kernel.plugins.registries import Registries

Role = Literal["backend", "mcp", "workers"]
TableRegistrar = Callable[[Any, str], None]  # (TableSpec, owner)


@dataclass
class BootReport:
    role: Role
    safe_mode: bool = False
    safe_mode_reason: str = ""
    builtins: LoadReport | None = None
    users: LoadReport | None = None
    rejected: dict[str, str] = field(default_factory=dict)
    isolated: dict[str, str] = field(default_factory=dict)
    disabled_builtins: tuple[str, ...] = ()
    distribution: str | None = None  # id of the distribution that shaped this boot
    excluded_builtins: tuple[str, ...] = ()  # builtins the distribution left out
    # builtin id -> why its on-demand dependencies are unavailable (booted without it)
    deps_missing: dict[str, str] = field(default_factory=dict)
    activation_events: dict[str, tuple[str, ...]] = field(default_factory=dict)
    duration_ms: float = 0.0
    _marker: BootMarker | None = None

    def mark_healthy(self) -> None:
        """Called by the host once it passed its own health probe; clears the crash counter."""
        if self._marker is not None:
            self._marker.exit()

    @property
    def user_plugin_ids(self) -> tuple[str, ...]:
        if self.users is None:
            return ()
        return tuple(p.plugin_id for p in self.users.loaded if not p.error)


def _state_of(reason: str) -> str:
    return reason.split(":", 1)[0]


def _persist_rejections(store: RegistryStore, rejected: dict[str, str]) -> None:
    for pid, reason in rejected.items():
        try:
            store.transition(pid, _state_of(reason), error=reason)  # type: ignore[arg-type]
        except RegistryError as exc:
            logger.debug(f"[plugins] cannot record {pid} state: {exc}")


def boot(
    role: Role,
    *,
    registries: Registries,
    cloud: bool,
    host_version: str,
    activator: Activator | None = None,
    register_table: TableRegistrar | None = None,
    store: RegistryStore | None = None,
    blocked_versions: dict[str, dict[str, str]] | None = None,
    stage2_deadline_s: float = 120.0,
    distribution: "DistributionResolution | None" = None,
) -> BootReport:
    started = time.perf_counter()
    report = BootReport(role=role)
    store = store or RegistryStore(path=registry_path())

    # ---- safe-mode decision (local only)
    marker: BootMarker | None = None
    if not cloud:
        marker = BootMarker(role)
        failures = marker.enter()
        if marker.safe_mode_due and not store.read().safe_mode if store.path.exists() else False:
            reason = f"{failures + 1} consecutive boots of {role} never reached health"
            store.set_safe_mode(True, reason=reason)
            logger.error(f"[plugins] entering SAFE MODE: {reason}")
        report._marker = marker

    found: Discovery = discover(
        cloud=cloud, user_registry_path=store.path, host_version=host_version, blocked_versions=blocked_versions
    )
    report.safe_mode = found.safe_mode
    if found.safe_mode and store.path.exists():
        report.safe_mode_reason = store.read().safe_mode_reason
    report.rejected = dict(found.rejected)
    if report.rejected:
        _persist_rejections(store, report.rejected)

    # ---- disabled builtins: undo their import-time registrations before anything is frozen
    report.disabled_builtins = tuple(found.disabled_builtins)
    for pid in found.disabled_builtins:
        removed = registries.remove_owner(pid)
        logger.info(f"[plugins] {pid}: disabled ({removed} contribution(s) removed)")

    # ---- on-demand builtin dependencies: probe / install; a builtin whose deps
    # are unavailable boots as if disabled (deps_missing, retry from the factory)
    from narranexus.kernel.plugins.install.builtin_deps import ensure_builtin_deps

    # ---- the distribution (spec section 19): its plugin set IS the stage-1 set —
    # builtins it leaves out lose their import-time registrations like a
    # disabled builtin, bundled plugins get their package and deps like a user
    # plugin but boot in stage 1 (fail-fast: they are the distribution's own code).
    stage1 = [m for m in found.manifests if m.is_builtin]
    if distribution is not None:
        distribution.raise_for_problems()
        report.distribution = distribution.spec.id
        selected = {m.id for m in distribution.manifests}
        left_out = tuple(m.id for m in stage1 if m.id not in selected)
        for pid in left_out:
            removed = registries.remove_owner(pid)
            logger.info(f"[plugins] {pid}: not in distribution {distribution.spec.id} ({removed} contribution(s) removed)")
        report.excluded_builtins = left_out
        disabled = set(found.disabled_builtins)
        stage1 = [m for m in distribution.manifests if m.id not in disabled]
        finder = plugin_finder()
        for pick in distribution.picks:
            if pick.path is not None and pick.id not in disabled:
                _prepare_user_plugin(pick.manifest, pick.path, finder, store)

    builtins = []
    for manifest in stage1:
        status = ensure_builtin_deps(manifest, cloud=cloud)
        if status.ok:
            builtins.append(manifest)
            continue
        report.deps_missing[manifest.id] = status.error or "dependencies missing"
        removed = registries.remove_owner(manifest.id)
        logger.warning(f"[plugins] {manifest.id}: deps_missing — booting without it ({removed} contribution(s) removed): {status.error}")

    # ---- stage 1: builtins (fail-fast inside load())
    report.builtins = load(registries, builtins, role=role)

    # ---- stage 2: user plugins, isolated
    users = [m for m in found.manifests if not m.is_builtin]
    if users and distribution is not None and not distribution.spec.runtime.user_plugins:
        logger.info(f"[plugins] distribution {distribution.spec.id} disallows runtime plugins: {len(users)} skipped")
        users = []
    if users:
        deadline = time.perf_counter() + stage2_deadline_s
        finder = plugin_finder()
        for manifest in users:
            path = found.paths[manifest.id]
            try:
                _prepare_user_plugin(manifest, path, finder, store)
            except Exception as exc:  # noqa: BLE001 — isolate
                report.isolated[manifest.id] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"[plugins] {manifest.id}: isolated before load: {exc}")
        loadable = [m for m in users if m.id not in report.isolated]
        report.users = load(registries, loadable, role=role)
        for entry in report.users.loaded:
            if entry.error:
                report.isolated[entry.plugin_id] = entry.error
        for pid, err in report.isolated.items():
            try:
                store.record_crash(pid, err)
            except RegistryError:
                pass
        # tables are pure data: register them even when the plugin never activates
        if register_table is not None and "backend.tables" in registries.paths():
            for entry in registries.registry_for("backend.tables").entries():
                if entry.owner.startswith("builtin.") or entry.owner in report.isolated:
                    continue
                try:
                    register_table(entry.factory(), entry.owner)
                except Exception as exc:  # noqa: BLE001
                    report.isolated[entry.owner] = f"table {entry.name!r}: {exc}"
                    logger.warning(f"[plugins] {entry.owner}: table {entry.name!r} refused: {exc}")
        if activator is not None:
            for manifest in loadable:
                if manifest.id in report.isolated:
                    continue
                report.activation_events[manifest.id] = activator.register(manifest)
        for manifest in loadable:
            if manifest.id not in report.isolated:
                try:
                    store.transition(manifest.id, "validated")
                    store.transition(manifest.id, "enabled")
                except RegistryError as exc:
                    logger.debug(f"[plugins] {manifest.id}: state not advanced: {exc}")
        if time.perf_counter() > deadline:
            logger.warning(f"[plugins] stage 2 exceeded its {stage2_deadline_s:.0f}s deadline")

    registries.freeze()
    report.duration_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        f"[plugins] {role} boot: {len(builtins)} builtin, {len(report.user_plugin_ids)} user plugin(s) loaded, "
        f"{len(report.isolated)} isolated, {len(report.rejected)} rejected, safe_mode={report.safe_mode}, "
        f"{report.duration_ms:.0f} ms"
    )
    return report


def _prepare_user_plugin(manifest: Manifest, path: Path, finder: Any, store: RegistryStore) -> None:
    """Synthetic package + private deps for one user plugin (no code runs yet)."""
    backend_pkg = manifest.backend.package if manifest.backend else "backend"
    backend_dir = path / backend_pkg
    if backend_dir.is_dir():
        install_synthetic_package(manifest.id, backend_dir)
    rec = store.read().plugins.get(manifest.id)
    mode = rec.mode if rec else "copy"
    deps = deps_dir(manifest.id, mode=mode, plugin_path=path)
    if deps.is_dir():
        finder.register_deps(manifest.id, deps)


__all__ = ["BootReport", "Role", "boot"]
