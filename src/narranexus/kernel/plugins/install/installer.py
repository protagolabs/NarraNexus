"""
@file_name: installer.py
@author: Bin Liang
@date: 2026-09-03
@description: The one install pipeline: fetch → validate → deps → place → register; plus uninstall, upgrade and update checks.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx
from loguru import logger

from narranexus.contracts import ManifestError, PluginError
from narranexus.kernel.plugins.compat import blocked_reason, host_version, select_version
from narranexus.kernel.plugins.install.deps import Runner, install_deps
from narranexus.kernel.plugins.install.sources import GitHubReleaseSource, GitHubRepoSource, LocalSource, Source, parse_source_spec
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.manifest import Manifest, load_manifest
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, VERSIONS_FILENAME, deps_dir, plugin_dir, plugin_home, registry_path
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins


class InstallError(PluginError):
    pass


@dataclass
class InstallResult:
    plugin_id: str
    version: str
    path: Path
    mode: str
    warnings: list[str] = field(default_factory=list)
    deps_installed: tuple[str, ...] = ()
    permissions: dict[str, Any] = field(default_factory=dict)
    restart_required: bool = True


@dataclass
class UpdateCheck:
    plugin_id: str
    installed: str
    available: str | None
    source: str


@dataclass
class Installer:
    store: RegistryStore = field(default_factory=lambda: RegistryStore(path=registry_path()))
    client: httpx.Client | None = None
    runner: Runner | None = None
    blocked: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    host: str = field(default_factory=host_version)

    # ------------------------------------------------------------- install

    def install(
        self,
        source: Source | str,
        *,
        installed_by: str = "user",
        scope: str = "global",
        permissions_acknowledged: bool = False,
        replace: bool = False,
    ) -> InstallResult:
        src = parse_source_spec(source) if isinstance(source, str) else source
        staging = Path(tempfile.mkdtemp(prefix="nx-plugin-install-", dir=str(plugin_home())))
        try:
            fetched = src.fetch(staging, self.client)
            manifest = self._validate(fetched.root)
            reason = blocked_reason(self.blocked, manifest.id, manifest.version)
            if reason:
                raise InstallError(f"{manifest.id} {manifest.version} is blocked: {reason}")
            existing = self.store.read().plugins.get(manifest.id)
            if existing is not None and not replace:
                raise InstallError(f"{manifest.id} is already installed ({existing.installed_version}); use upgrade")
            if existing is not None and existing.mode == "copy" and fetched.mode != "link":
                # replacing: the old copy goes away only after the new one is in place
                pass
            deps = self._install_deps(manifest, fetched.root, fetched.mode)
            final_path = self._place(manifest, fetched.root, fetched.mode, existing)
            record = PluginRecord(
                path=str(final_path),
                mode="link" if fetched.mode == "link" else "copy",
                source=fetched.record,
                installed_version=manifest.version,
                scope=scope,
                warnings=list(fetched.warnings),
                permissions_acknowledged=permissions_acknowledged,
                installed_by=installed_by,
            )
            if existing is not None:
                self.store.unregister(manifest.id)
            self.store.register(manifest.id, record)
            logger.info(f"[plugins] installed {manifest.id} {manifest.version} from {src.describe()} → {final_path}")
            return InstallResult(
                plugin_id=manifest.id,
                version=manifest.version,
                path=final_path,
                mode=record.mode,
                warnings=list(fetched.warnings),
                deps_installed=deps,
                permissions=manifest.permissions.model_dump(mode="json"),
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _validate(self, root: Path) -> Manifest:
        mpath = root / MANIFEST_FILENAME
        try:
            return load_manifest(mpath, tree=slot_tree_with_builtins(), host_version=self.host)
        except ManifestError as exc:
            versions = root / VERSIONS_FILENAME
            if versions.is_file():
                try:
                    fitting = select_version(json.loads(versions.read_text(encoding="utf-8")), self.host)
                except (ValueError, TypeError):
                    fitting = None
                if fitting:
                    raise InstallError(f"{exc}. versions.json suggests {fitting} for this host; install that tag instead") from None
            raise InstallError(str(exc)) from None

    def _install_deps(self, manifest: Manifest, root: Path, mode: str) -> tuple[str, ...]:
        if manifest.backend is None or not manifest.backend.pip or manifest.install.deps == "on_demand":
            return ()
        target = deps_dir(manifest.id, mode="link" if mode == "link" else "copy", plugin_path=root)
        result = install_deps(manifest.backend.pip, target, runner=self.runner)
        return result.requirements if result else ()

    def _place(self, manifest: Manifest, root: Path, mode: str, existing: PluginRecord | None) -> Path:
        if mode == "link":
            return root
        dest = plugin_dir(manifest.id)
        if dest.exists():
            backup = dest.with_name(dest.name + f".old-{int(time.time())}")
            shutil.move(str(dest), str(backup))
            shutil.move(str(root), str(dest))
            shutil.rmtree(backup, ignore_errors=True)
        else:
            shutil.move(str(root), str(dest))
        # deps were installed into <staging>/pyenv; they moved with the directory
        return dest

    # ----------------------------------------------------------- uninstall

    def uninstall(self, plugin_id: str, *, purge_files: bool = True) -> None:
        reg = self.store.read()
        rec = reg.plugins.get(plugin_id)
        if rec is None:
            raise InstallError(f"{plugin_id} is not installed")
        self.store.unregister(plugin_id)
        if purge_files and rec.mode == "copy":
            path = Path(rec.path)
            if path.exists() and plugin_home().resolve() in path.resolve().parents:
                shutil.rmtree(path, ignore_errors=True)
        if rec.mode == "link":
            deps = deps_dir(plugin_id, mode="link")
            if deps.exists():
                shutil.rmtree(deps, ignore_errors=True)
        logger.info(f"[plugins] uninstalled {plugin_id} (files {'removed' if purge_files else 'kept'})")

    # ------------------------------------------------------------- upgrade

    def check_update(self, plugin_id: str) -> UpdateCheck:
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise InstallError(f"{plugin_id} is not installed")
        available: str | None = None
        if rec.source.type == "github" and rec.source.repo:
            client = self.client or httpx.Client()
            resp = client.get(f"https://api.github.com/repos/{rec.source.repo}/releases/latest", timeout=20.0, follow_redirects=True)
            if resp.status_code == 200:
                tag = str(resp.json().get("tag_name") or "").lstrip("v")
                available = tag if tag and tag != rec.installed_version.lstrip("v") else None
        return UpdateCheck(plugin_id, rec.installed_version, available, rec.source.type)

    def upgrade(self, plugin_id: str, *, to_tag: str | None = None) -> InstallResult:
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise InstallError(f"{plugin_id} is not installed")
        if rec.source.type == "github":
            source: Source = GitHubReleaseSource(rec.source.repo, to_tag or "")
        elif rec.source.type == "github_repo":
            source = GitHubRepoSource(rec.source.repo, to_tag or rec.source.ref)
        else:
            source = LocalSource(Path(rec.path), mode=rec.mode)
        return self.install(
            source, installed_by=rec.installed_by, scope=rec.scope, permissions_acknowledged=rec.permissions_acknowledged, replace=True
        )


__all__ = ["InstallError", "InstallResult", "Installer", "UpdateCheck"]
