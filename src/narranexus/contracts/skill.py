"""
@file_name: skill.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for skill contributions (slot ``content.skills``).

A skill is a directory with a ``SKILL.md`` (the OpenClaw format and its
aliases). Plugins point at such directories; the skill module scans them
like workspace skills and records ``kind`` so the catalog can tell a
plugin-shipped skill from a marketplace or hand-written one.

Contract version: ``API_VERSIONS["skill"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SkillKind = Literal["plugin", "marketplace", "workspace", "builtin"]


@dataclass(frozen=True)
class SkillSpec:
    path: Path
    kind: SkillKind = "plugin"

    def __post_init__(self) -> None:
        if self.kind not in ("plugin", "marketplace", "workspace", "builtin"):
            raise ValueError(f"unknown skill kind {self.kind!r}")

    @property
    def manifest_path(self) -> Path:
        return self.path / "SKILL.md"


__all__ = ["SkillKind", "SkillSpec"]
