"""
@file_name: detector.py
@author: NetMind.AI
@date: 2026-07-21
@description: Framework detection for Agent Migration.

Signal-driven detection of Claude Code / Hermes / OpenClaw / Codex agent
configs on the local filesystem. Detect-only — never reads secrets, never
writes. Mirrors the source-dir conventions Hermes `import-agent` targets.

Two entry points:
- ``detect_all(home)``   — probe the standard home locations, return every
  framework found (multi-framework coexistence, PRD P1).
- ``classify_path(path)``— classify one explicit directory the user pointed at.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from xyz_agent_context.schema.migration_schema import (
    Confidence,
    Framework,
    FrameworkDetection,
)

# Per-framework signal table. ``home_dirs`` are the standard locations probed by
# detect_all (relative to the user's home). ``strong`` files, when present in the
# scanned dir, give high confidence; ``weak`` files alone give medium.
_SIGNALS: dict[Framework, dict] = {
    "claude_code": {
        "home_dirs": [".claude"],
        "strong": [".mcp.json", "settings.json", "CLAUDE.md"],
        "weak": ["commands", "skills", "projects", "CLAUDE.local.md"],
    },
    "hermes": {
        "home_dirs": [".hermes"],
        "strong": ["config.yaml", "SOUL.md"],
        "weak": ["MEMORY.md", "USER.md", "AGENTS.md", "skills"],
    },
    "openclaw": {
        # legacy dir names Hermes also recognises
        "home_dirs": [".openclaw", ".clawdbot", ".moltbot"],
        "strong": ["openclaw.json", "clawdbot.json", "moltbot.json", "SOUL.md"],
        "weak": ["MEMORY.md", "USER.md", "skills"],
    },
    "codex": {
        "home_dirs": [".codex"],
        "strong": ["config.toml", "config.json", "AGENTS.md"],
        "weak": ["instructions.md", "prompts"],
    },
}


def _score_dir(path: Path, framework: Framework) -> Optional[FrameworkDetection]:
    """Score one directory against one framework's signals. None = no match."""
    if not path.exists() or not path.is_dir():
        return None
    sig = _SIGNALS[framework]
    strong_hits = [f for f in sig["strong"] if (path / f).exists()]
    weak_hits = [f for f in sig["weak"] if (path / f).exists()]
    # The dir itself being a known home dir is a signal too.
    dir_is_home = path.name in sig["home_dirs"]

    if not strong_hits and not weak_hits and not dir_is_home:
        return None

    confidence: Confidence
    if strong_hits and (dir_is_home or len(strong_hits) >= 2):
        confidence = "high"
    elif strong_hits or dir_is_home:
        confidence = "medium"
    else:
        confidence = "low"

    signals = []
    if dir_is_home:
        signals.append(f"dir={path.name}")
    signals += [f"has:{f}" for f in strong_hits + weak_hits]
    return FrameworkDetection(
        framework=framework, path=str(path), confidence=confidence, signals=signals
    )


def classify_path(path: str | Path) -> FrameworkDetection:
    """Classify one explicit directory. Falls back to 'custom' (low) if nothing
    matches — the Custom-Importer heuristic + LLM fallback handles that case."""
    p = Path(path).expanduser()
    best: Optional[FrameworkDetection] = None
    order = {"high": 3, "medium": 2, "low": 1}
    for fw in _SIGNALS:
        d = _score_dir(p, fw)
        if d and (best is None or order[d.confidence] > order[best.confidence]):
            best = d
    if best is not None:
        return best
    # Custom fallback: any of the generic instruction/config hints?
    hints = [f for f in ("AGENTS.md", "CLAUDE.md", "config.yaml", "config.json")
             if (p / f).exists()]
    return FrameworkDetection(
        framework="custom",
        path=str(p),
        confidence="low",
        signals=[f"has:{f}" for f in hints] or ["no-known-signals"],
    )


def detect_all(home: str | Path | None = None) -> List[FrameworkDetection]:
    """Probe the standard home locations for every framework. Returns highest-
    confidence hit per framework, so a machine with several frameworks yields
    several detections."""
    base = Path(home).expanduser() if home else Path.home()
    out: List[FrameworkDetection] = []
    for fw, sig in _SIGNALS.items():
        best: Optional[FrameworkDetection] = None
        order = {"high": 3, "medium": 2, "low": 1}
        for d_name in sig["home_dirs"]:
            d = _score_dir(base / d_name, fw)
            if d and (best is None or order[d.confidence] > order[best.confidence]):
                best = d
        if best is not None:
            out.append(best)
    return out
