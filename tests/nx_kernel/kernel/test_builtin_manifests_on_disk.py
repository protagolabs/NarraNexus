"""
@file_name: test_builtin_manifests_on_disk.py
@author: Bin Liang
@date: 2026-09-07
@description: The builtin manifests have ONE home — plugins/<id>/narranexus-plugin.json — and the kernel's BUILTIN_PLUGINS names every plugin directory exactly once, in load order, with the package its manifest ships in; every manifest validates against the kernel tree and its id is its directory.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.kernel.plugins.builtins import BUILTIN_MANIFEST_DATA, BUILTIN_PLUGINS, builtin_manifests, slot_tree_with_builtins
from narranexus.kernel.plugins.manifest import parse_manifest

REPO = Path(__file__).resolve().parents[3]


def test_every_plugin_directory_is_listed_once_with_its_package():
    dirs = sorted(p.name for p in (REPO / "plugins").iterdir() if p.is_dir() and p.name.startswith("builtin."))
    listed = [pid for pid, _ in BUILTIN_PLUGINS]
    assert sorted(listed) == dirs and len(listed) == len(set(listed))
    for pid, pkg in BUILTIN_PLUGINS:
        assert (REPO / "plugins" / pid / "src" / "narranexus_plugins" / pkg).is_dir(), (pid, pkg)


def test_manifests_come_from_disk_and_validate():
    # Against the tree WITH every builtin's declarations: a framework plugin
    # provides into the slot builtin.turn declares.
    tree = slot_tree_with_builtins()
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


def _ids() -> list[str]:
    return [pid for pid, _ in BUILTIN_PLUGINS]


def test_every_listing_of_the_builtins_agrees_with_the_plugin_directories():
    """The builtin set is written down in several places (each with its own
    reason to exist); none may drift from ``plugins/``: the uv workspace
    dependencies and sources, pyright's include list, the uv lock, and every
    official distribution (which must classify each builtin as shipped or
    excluded)."""
    import tomllib

    ids = _ids()
    dist_names = {pid: tomllib.loads((REPO / "plugins" / pid / "pyproject.toml").read_text())["project"]["name"] for pid in ids}
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    deps = {d for d in pyproject["project"]["dependencies"] if d.startswith("narranexus-plugin-")}
    assert deps == set(dist_names.values())
    sources = {name for name, spec in pyproject["tool"]["uv"]["sources"].items() if name.startswith("narranexus-plugin-")}
    assert sources == set(dist_names.values())
    assert all(spec == {"workspace": True} for name, spec in pyproject["tool"]["uv"]["sources"].items() if name in sources)
    lock = (REPO / "uv.lock").read_text()
    assert all(f'name = "{name}"' in lock for name in dist_names.values())
    pyright = (REPO / "pyrightconfig.json").read_text()  # commented JSON — substring check
    assert all(f'"plugins/{pid}/src"' in pyright for pid in ids)
    for dist_dir in sorted(p for p in (REPO / "distributions").iterdir() if p.is_dir()):
        spec = json.loads((dist_dir / "narranexus-dist.json").read_text())
        classified = set(spec["plugins"]) | set(spec.get("excludes", []))
        # A distribution may ship third-party plugins too; every BUILTIN must be classified.
        assert {p for p in classified if p.startswith("builtin.")} == set(ids), dist_dir.name
        assert not (set(spec["plugins"]) & set(spec.get("excludes", []))), dist_dir.name
