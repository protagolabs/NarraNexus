"""
@file_name: service.py
@author: Bin Liang
@date: 2026-09-03
@description: ``FactoryService`` — every mutation of user-plugin state goes through here, and from here only through ``lifecycle``/``Installer``.

The service composes the kernel pieces the factory page needs: the
registry store (state), the installer (sources/deps/placement), the index
(search + blocklist), the bisect helper, the last boot report (what
loaded / what was isolated) and a bounded per-plugin error log fed by the
frontend error sink. Cloud deployments get a read-only view: every mutation
raises ``CloudManaged`` so the route answers 403 before anything runs.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from narranexus.hosts.boot import BootReport
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.plugins.bisect import Bisect
from narranexus.kernel.plugins.install import Installer, InstallResult
from narranexus.kernel.plugins.install.index import Index
from narranexus.kernel.plugins.lifecycle import RegistryError, RegistryStore
from narranexus.kernel.plugins.manifest import Manifest, derive_activation_events, load_manifest
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, frontend_dist_dir, plugin_home, registry_path
from narranexus.kernel.plugins.slots import build_kernel_slot_tree

ERROR_LOG_LIMIT = 50


class CloudManaged(Exception):
    """Mutations are refused in cloud mode (plugins are baked into the image)."""


class NotInstalled(KeyError):
    pass


@dataclass
class UiError:
    at: float
    kind: str
    message: str
    stack: str = ""


@dataclass
class FactoryService:
    store: RegistryStore = field(default_factory=lambda: RegistryStore(path=registry_path()))
    installer: Installer | None = None
    index: Index | None = None
    boot_report: BootReport | None = None
    _errors: dict[str, deque[UiError]] = field(default_factory=dict)

    # ------------------------------------------------------------ helpers

    def _installer(self) -> Installer:
        if self.installer is None:
            blocked = self._index().blocked() if self.index is not None else {}
            self.installer = Installer(store=self.store, blocked=blocked)
        return self.installer

    def _index(self) -> Index:
        if self.index is None:
            self.index = Index(cache_dir=plugin_home() / ".index-cache")
        return self.index

    @staticmethod
    def _guard_mutation() -> None:
        if is_cloud_mode():
            raise CloudManaged("plugins are managed by the platform in cloud mode")

    def _manifest_of(self, path: Path) -> Manifest | None:
        try:
            return load_manifest(path / MANIFEST_FILENAME, tree=build_kernel_slot_tree())
        except Exception:  # noqa: BLE001 — a broken manifest is reported through state, not here
            return None

    # ------------------------------------------------------------ listing

    def list(self) -> dict[str, Any]:
        reg = self.store.read()
        boot = self.boot_report
        rows: list[dict[str, Any]] = []
        for pid, rec in sorted(reg.plugins.items()):
            manifest = self._manifest_of(Path(rec.path))
            rows.append(
                {
                    "id": pid,
                    "display_name": manifest.display_name if manifest else pid,
                    "description": manifest.description if manifest else "",
                    "version": rec.installed_version,
                    "mode": rec.mode,
                    "path": rec.path,
                    "source": rec.source.model_dump(),
                    "enabled": rec.enabled,
                    "state": rec.state,
                    "scope": rec.scope,
                    "last_error": rec.last_error,
                    "crash_count": rec.crash_count,
                    "warnings": list(rec.warnings),
                    "permissions": manifest.permissions.model_dump() if manifest else {},
                    "permissions_acknowledged": rec.permissions_acknowledged,
                    "installed_by": rec.installed_by,
                    "installed_at": rec.installed_at,
                    "provides": sorted(manifest.provides) if manifest else [],
                    "frontend": manifest.frontend.model_dump(mode="json") if manifest and manifest.frontend else None,
                    "activation_events": list(derive_activation_events(manifest)) if manifest else [],
                    "protected": bool(manifest.protected) if manifest else False,
                    "size": manifest.size.model_dump() if manifest else {},
                    "loaded": bool(boot and pid in boot.user_plugin_ids),
                    "isolated": (boot.isolated.get(pid) if boot else None),
                    "recent_errors": len(self._errors.get(pid, ())),
                }
            )
        return {
            "plugins": rows,
            "safe_mode": reg.safe_mode,
            "safe_mode_reason": reg.safe_mode_reason,
            "bisect": reg.bisect.model_dump() if reg.bisect else None,
            "cloud_managed": is_cloud_mode(),
            "boot": (
                {
                    "role": boot.role,
                    "loaded": list(boot.user_plugin_ids),
                    "isolated": dict(boot.isolated),
                    "rejected": dict(boot.rejected),
                    "duration_ms": boot.duration_ms,
                }
                if boot
                else None
            ),
        }

    # ---------------------------------------------------------- mutations

    def install(self, source: str, *, permissions_acknowledged: bool = False, scope: str = "global") -> InstallResult:
        self._guard_mutation()
        return self._installer().install(source, permissions_acknowledged=permissions_acknowledged, scope=scope)

    def uninstall(self, plugin_id: str, *, purge_files: bool = True) -> None:
        self._guard_mutation()
        try:
            self._installer().uninstall(plugin_id, purge_files=purge_files)
        except Exception as exc:  # noqa: BLE001
            raise NotInstalled(str(exc)) from exc

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        self._guard_mutation()
        try:
            rec = self.store.set_enabled(plugin_id, enabled)
        except RegistryError as exc:
            raise NotInstalled(str(exc)) from exc
        return rec.model_dump()

    def acknowledge_permissions(self, plugin_id: str) -> dict[str, Any]:
        self._guard_mutation()

        def _mutate(reg):
            rec = reg.plugins.get(plugin_id)
            if rec is None:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            rec.permissions_acknowledged = True

        try:
            return self.store.update(_mutate).plugins[plugin_id].model_dump()
        except RegistryError as exc:
            raise NotInstalled(str(exc)) from exc

    def rollback(self) -> dict[str, Any]:
        self._guard_mutation()
        reg = self.store.rollback_to_lkg()
        return {"plugins": sorted(reg.plugins), "safe_mode": reg.safe_mode}

    def leave_safe_mode(self) -> dict[str, Any]:
        self._guard_mutation()
        reg = self.store.set_safe_mode(False)
        return {"safe_mode": reg.safe_mode}

    def upgrade(self, plugin_id: str) -> InstallResult:
        self._guard_mutation()
        try:
            return self._installer().upgrade(plugin_id)
        except Exception as exc:  # noqa: BLE001
            raise NotInstalled(str(exc)) from exc

    def check_update(self, plugin_id: str) -> dict[str, Any]:
        try:
            return asdict(self._installer().check_update(plugin_id))
        except Exception as exc:  # noqa: BLE001
            raise NotInstalled(str(exc)) from exc

    # -------------------------------------------------------------- bisect

    def bisect_start(self) -> dict[str, Any]:
        self._guard_mutation()
        return asdict(Bisect(self.store).start())

    def bisect_answer(self, good: bool) -> dict[str, Any]:
        self._guard_mutation()
        return asdict(Bisect(self.store).answer(good=good))

    def bisect_stop(self) -> dict[str, Any]:
        self._guard_mutation()
        Bisect(self.store).stop()
        return {"stopped": True}

    # --------------------------------------------------------------- index

    def search_index(self, query: str = "") -> list[dict[str, Any]]:
        return [asdict(e) for e in self._index().search(query)]

    # -------------------------------------------------------------- errors

    def record_error(self, plugin_id: str, *, kind: str, message: str, stack: str = "") -> int:
        log = self._errors.setdefault(plugin_id, deque(maxlen=ERROR_LOG_LIMIT))
        log.append(UiError(at=time.time(), kind=kind, message=message[:2000], stack=stack[:8000]))
        return len(log)

    def errors(self, plugin_id: str) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._errors.get(plugin_id, ())]

    # -------------------------------------------------------------- assets

    def asset_path(self, plugin_id: str, relative: str) -> Path:
        """Resolve a frontend asset under the plugin's ``frontend/dist`` with a traversal guard."""
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise NotInstalled(plugin_id)
        root = frontend_dist_dir(Path(rec.path)).resolve()
        candidate = (root / relative).resolve()
        if root not in candidate.parents and candidate != root:
            raise PermissionError(f"{relative!r} escapes the plugin's asset directory")
        if not candidate.is_file():
            raise FileNotFoundError(relative)
        return candidate


__all__ = ["CloudManaged", "ERROR_LOG_LIMIT", "FactoryService", "NotInstalled", "UiError"]
