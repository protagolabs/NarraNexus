"""
@file_name: test_builtin_manifests_on_disk.py
@author: Bin Liang
@date: 2026-09-07
@description: The builtin manifests have ONE home — plugins/<id>/narranexus-plugin.json — and the kernel's BUILTIN_PLUGINS names every plugin directory exactly once, in load order, with the package its manifest ships in; every manifest validates against the kernel tree and its id is its directory.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.kernel.plugins.builtins import BUILTIN_MANIFEST_DATA, BUILTIN_PLUGINS, builtin_manifests
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.slots import build_kernel_slot_tree

REPO = Path(__file__).resolve().parents[3]


def test_every_plugin_directory_is_listed_once_with_its_package():
    dirs = sorted(p.name for p in (REPO / "plugins").iterdir() if p.is_dir() and p.name.startswith("builtin."))
    listed = [pid for pid, _ in BUILTIN_PLUGINS]
    assert sorted(listed) == dirs and len(listed) == len(set(listed))
    for pid, pkg in BUILTIN_PLUGINS:
        assert (REPO / "plugins" / pid / "src" / "narranexus_plugins" / pkg).is_dir(), (pid, pkg)


def test_manifests_come_from_disk_and_validate():
    tree = build_kernel_slot_tree()
    for pid, _ in BUILTIN_PLUGINS:
        on_disk = json.loads((REPO / "plugins" / pid / "narranexus-plugin.json").read_text())
        assert on_disk["id"] == pid
        assert on_disk in BUILTIN_MANIFEST_DATA
        parse_manifest(on_disk, tree=tree, allow_builtin=True)
    assert [m.id for m in builtin_manifests()] == [pid for pid, _ in BUILTIN_PLUGINS]


def test_every_plugin_wheel_carries_its_manifest():
    import tomllib

    for pid, pkg in BUILTIN_PLUGINS:
        cfg = tomllib.loads((REPO / "plugins" / pid / "pyproject.toml").read_text())
        include = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        assert include["narranexus-plugin.json"] == f"narranexus_plugins/{pkg}/narranexus-plugin.json", pid
