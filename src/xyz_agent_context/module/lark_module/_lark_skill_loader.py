"""
@file_name: _lark_skill_loader.py
@date: 2026-04-16
@description: Load Lark CLI Skill docs (SKILL.md) for the lark_skill MCP tool.

Skills are installed via `npx skills add larksuite/cli -y -g` and live at
~/.claude/skills/lark-*/SKILL.md. The Agent reads these on-demand to learn
how to use specific lark-cli domains.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from loguru import logger

# Directories to search for Skill docs (in priority order)
_SKILL_SEARCH_PATHS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
]


def _find_skill_dirs() -> dict[str, Path]:
    """Discover lark-* skill directories. Returns {name: dir_path}."""
    skills: dict[str, Path] = {}
    env_dir = os.environ.get("LARK_SKILLS_DIR")
    search_paths = list(_SKILL_SEARCH_PATHS)
    if env_dir:
        search_paths.insert(0, Path(env_dir))

    for base in search_paths:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and child.name.startswith("lark-"):
                skill_file = child / "SKILL.md"
                if skill_file.is_file():
                    name = child.name  # e.g. "lark-im"
                    if name not in skills:  # First found wins
                        skills[name] = child
    return skills


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from markdown."""
    if content.startswith("---"):
        match = re.match(r"^---\n.*?\n---\n?", content, re.DOTALL)
        if match:
            return content[match.end():].lstrip()
    return content


def get_available_skills() -> list[str]:
    """Return list of available skill names (e.g. ['lark-im', 'lark-calendar'])."""
    return sorted(_find_skill_dirs().keys())


def load_skill_content(name: str) -> Optional[str]:
    """Load a specific skill's SKILL.md content (with frontmatter stripped).

    Returns None if not found.
    """
    dirs = _find_skill_dirs()
    skill_dir = dirs.get(name)
    if not skill_dir:
        return None
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return None
    try:
        content = skill_file.read_text(encoding="utf-8")
        return _strip_frontmatter(content)
    except Exception as e:
        logger.warning(f"Failed to read skill {name}: {e}")
        return None


def get_all_skills() -> dict[str, str]:
    """Load all available skill contents. Returns {name: content}."""
    result = {}
    for name in _find_skill_dirs():
        content = load_skill_content(name)
        if content:
            result[name] = content
    return result
