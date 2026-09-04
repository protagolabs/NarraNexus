"""
@file_name: test_skill_module_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.skills`: the manifest file equals the host's builtin manifest, the facade resolves the module class, and the package imports only the engine.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_facade():
    from narranexus.kernel.plugins.builtins import BUILTIN_MANIFEST_DATA
    from narranexus_plugins.skill_module import api

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    assert on_disk == next(d for d in BUILTIN_MANIFEST_DATA if d["id"] == "builtin.skills")
    cls = api.module_class()
    assert cls.__module__.startswith("narranexus_plugins.skill_module") and cls.get_config().name == cls.__name__
