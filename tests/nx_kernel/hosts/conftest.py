"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-09-03
@description: Fixture plugins on disk + an isolated plugin home for boot tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.kernel.plugins import importer
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME


@pytest.fixture
def plugin_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    yield home
    for pid in ("acme.ok", "acme.bad", "acme.dep", "acme.tables", "acme.a-b", "acme.a_b"):
        importer.uninstall_synthetic_package(pid)
        importer.plugin_finder().unregister_deps(pid)


def make_plugin(home: Path, pid: str, *, version="1.0.0", body="def activate(ctx):\n    ctx.log.info('hi')\n", extra=None, min_app="0.0.0") -> Path:
    root = home / pid
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "__init__.py").write_text(body)
    manifest = {
        "id": pid,
        "version": version,
        "displayName": pid,
        "minAppVersion": min_app,
        "hosts": ["backend", "mcp", "workers"],
        "backend": {"activate": True},
        "activationEvents": ["onStartup"],
    }
    manifest.update(extra or {})
    (root / "narranexus-plugin.json").write_text(json.dumps(manifest))
    return root


def register(home: Path, pid: str, path: Path, **kw) -> RegistryStore:
    store = RegistryStore(path=home / "registry.json", lkg=home / "registry.lkg.json")
    store.register(pid, PluginRecord(path=str(path), installed_version="1.0.0", **kw))
    return store
