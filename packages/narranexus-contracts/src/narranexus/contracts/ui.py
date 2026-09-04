"""
@file_name: ui.py
@author: Bin Liang
@date: 2026-09-03
@description: The frontend shell as a slot contract (``ui``), mirrored on the Python side.

The real frontend contribution registries live in TypeScript
(``frontend/src/platform/registries``). The Python side only needs to name
the shell so a distribution can bind its own; ``Shell`` is the data the
backend needs to serve it (where the built assets are, which entry file).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Shell:
    """A built frontend shell: asset directory and entry document."""

    id: str
    dist_dir: str
    entry: str = "index.html"


@dataclass(frozen=True)
class Theme:
    """A frontend theme contribution: which design tokens it overrides (the frontend validates against ``@theme``)."""

    id: str
    display_name: str
    tokens: Mapping[str, str] = field(default_factory=dict)
    dark: bool = False


__all__ = ["Shell", "Theme"]
