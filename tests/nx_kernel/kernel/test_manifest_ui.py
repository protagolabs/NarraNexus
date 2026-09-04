"""
@file_name: test_manifest_ui.py
@author: Bin Liang
@date: 2026-09-03
@description: frontend.ui declarations derive onPage/onPanel/onCommand activation events and are strict.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import ManifestError
from narranexus.kernel.plugins.manifest import derive_activation_events, parse_manifest
from narranexus.kernel.plugins.registries import Registries


def test_ui_declarations_derive_activation_events():
    tree = Registries().slots
    m = parse_manifest(
        {
            "id": "acme.w",
            "version": "1.0.0",
            "displayName": "W",
            "frontend": {
                "entry": "plugin.js",
                "ui": {"pages": [{"id": "acme.w.home", "path": "x/w"}], "panels": [{"id": "acme.w.p"}], "commands": [{"id": "acme.w.c", "label": "Go"}]},
            },
        },
        tree=tree,
    )
    assert derive_activation_events(m) == ("onPage:acme.w.home", "onPanel:acme.w.p", "onCommand:acme.w.c")
    assert m.frontend is not None and m.frontend.ui.pages[0].guard == "protected"
    with pytest.raises(ManifestError):
        parse_manifest({"id": "acme.w", "version": "1.0.0", "displayName": "W", "frontend": {"entry": "p.js", "ui": {"pages": [{"id": "x"}]}}}, tree=tree)
