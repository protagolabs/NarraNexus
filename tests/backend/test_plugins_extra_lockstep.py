"""
@file_name: test_plugins_extra_lockstep.py
@author: NarraNexus
@date: 2026-08-28
@description: Executable guard for the `--extra plugins` lockstep.

The coding-agent SDKs (claude-agent-sdk / openai-codex) live in the optional
`[project.optional-dependencies].plugins` extra, kept OUT of the base install so
the local build stays light. Every place that must RUN a framework has to pull
them back in with `uv sync ... --extra plugins`; every LOCAL place must NOT
(that is the whole point of the slim-down).

This test SCANS the whole repo (every `.sh` / `.yml` / `Dockerfile*` / Makefile)
for `uv sync` command lines and requires each such file to be explicitly
classified below. A NEW deploy/CI entry point that runs `uv sync` — and forgets
to classify itself — fails `test_every_uv_sync_file_is_classified` instead of
silently shipping claude_code / codex_cli dead. Same "mirror a fact + assert
they agree" shape as test_claude_cli_pin.py.

NOTE: the deploy repo's Dockerfile.executor / Dockerfile.python are the OTHER
half of this lockstep; they live in a separate repo and are guarded on that side.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# CLOUD/CI: `uv sync` here runs the agent frameworks → MUST carry --extra plugins.
_CLOUD_SYNC_FILES = frozenset({
    ".github/workflows/ci.yml",
    "docker/Dockerfile.manyfold",
    "scripts/release/deploy-cloud.sh",
})
# LOCAL: the lightweight path → `uv sync` MUST NOT carry --extra plugins.
_LOCAL_SYNC_FILES = frozenset({
    "run.sh",
    "scripts/dev/.dev-local-safe.sh",
})

_SKIP_DIRS = {".git", "node_modules", ".mindflow", ".venv", "dist", "reference"}


def _is_command_file(path: Path) -> bool:
    if any(part in _SKIP_DIRS for part in path.parts):
        return False
    return (
        path.suffix in (".sh", ".yml", ".yaml")
        or path.name.startswith("Dockerfile")
        or path.name == "Makefile"
    )


def _uv_sync_command_lines(path: Path) -> list[str]:
    """Logical lines that RUN `uv sync` (backslash continuations joined),
    excluding comments and echo/printf string lines that merely mention it."""
    text = path.read_text(encoding="utf-8", errors="replace")
    joined = re.sub(r"\\\n", " ", text)  # fold shell / Dockerfile line continuations
    out = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("echo", "printf", '"', "'", "@echo")):
            continue
        if "uv sync" in stripped:
            out.append(stripped)
    return out


def _all_files_running_uv_sync() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _REPO.rglob("*"):
        if not path.is_file() or not _is_command_file(path):
            continue
        lines = _uv_sync_command_lines(path)
        if lines:
            found[str(path.relative_to(_REPO))] = lines
    return found


def test_every_uv_sync_file_is_classified():
    """A repo file that runs `uv sync` MUST be in exactly one of the two lists —
    this is what makes a NEW forgotten entry point fail here."""
    classified = _CLOUD_SYNC_FILES | _LOCAL_SYNC_FILES
    unclassified = sorted(set(_all_files_running_uv_sync()) - classified)
    assert not unclassified, (
        "these files run `uv sync` but are not classified cloud/local in "
        "test_plugins_extra_lockstep.py — classify them:\n  " + "\n  ".join(unclassified)
    )


@pytest.mark.parametrize("rel", sorted(_CLOUD_SYNC_FILES))
def test_cloud_sync_pulls_plugins_extra(rel):
    lines = _uv_sync_command_lines(_REPO / rel)
    assert lines, f"{rel} no longer runs `uv sync` — update this guard"
    for line in lines:
        assert "--extra plugins" in line, (
            f"{rel}: cloud/CI `uv sync` missing `--extra plugins` → claude_code /"
            f" codex_cli will break there:\n    {line}"
        )


@pytest.mark.parametrize("rel", sorted(_LOCAL_SYNC_FILES))
def test_local_sync_stays_light(rel):
    for line in _uv_sync_command_lines(_REPO / rel):
        assert "--extra plugins" not in line, (
            f"{rel}: local `uv sync` pulls `--extra plugins`, defeating the "
            f"lightweight build:\n    {line}"
        )
