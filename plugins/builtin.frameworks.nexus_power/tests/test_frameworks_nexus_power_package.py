"""
@file_name: test_frameworks_nexus_power_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.frameworks.nexus_power`: the manifest file equals the host's builtin manifest and every provided symbol resolves inside this package.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_provides():
    from narranexus.kernel.plugins.builtins import BUILTIN_MANIFEST_DATA
    from narranexus.kernel.plugins.loader import resolve_symbol

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    data = next(d for d in BUILTIN_MANIFEST_DATA if d["id"] == "builtin.frameworks.nexus_power")
    assert on_disk == data
    for refs in data["provides"].values():
        for ref in ([refs] if isinstance(refs, str) else refs):
            assert ref.startswith(("narranexus_plugins.frameworks_nexus_power", "narranexus.platform.turn.pipeline")), ref
            assert resolve_symbol(ref) is not None
