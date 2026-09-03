"""
@file_name: _approval.py
@author: Bin Liang
@date: 2026-09-03
@description: Approval (characterization) snapshot helper for zero-behavior-change refactors.

A test renders a deterministic JSON view of some surface (routes, tables,
registries) and calls ``approve(name, value)``. The first run writes the
golden file; every later run must match it byte for byte. After a reviewed,
intentional behavior change, regenerate with ``NX_UPDATE_SNAPSHOTS=1``.
Timestamps / ids must be scrubbed by the caller before approving.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent / "golden"


def render(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def approve(name: str, value: Any) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    rendered = render(value)
    if os.environ.get("NX_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    current = path.read_text(encoding="utf-8")
    assert current == rendered, (
        f"approval snapshot {name!r} changed. If the behavior change is intended and "
        f"reviewed, regenerate with NX_UPDATE_SNAPSHOTS=1; otherwise this is a regression."
    )
