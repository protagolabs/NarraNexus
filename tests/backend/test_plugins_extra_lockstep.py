"""
@file_name: test_plugins_extra_lockstep.py
@author: NarraNexus
@date: 2026-08-28
@description: Executable guard for the `--extra plugins` lockstep.

The coding-agent SDKs (claude-agent-sdk / openai-codex) live in the optional
`[project.optional-dependencies].plugins` extra, kept OUT of the base install so
the local build stays light. Every place that must RUN a framework has to pull
them back in with `uv sync ... --extra plugins`; every LOCAL place must NOT
(that is the whole point of the slim-down). This test turns that split — which
otherwise lives only in comments scattered across five files — into a check, so
a new deploy/CI entry point that forgets the flag fails here instead of silently
shipping claude_code / codex_cli dead.

Same "mirror a fact across files + assert they agree" shape as
test_claude_cli_pin.py. NOTE: the deploy repo's Dockerfile.executor /
Dockerfile.python are the OTHER half of this lockstep; they cannot be reached
from here (separate repo) and are guarded on that side.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# Files whose `uv sync` runs on cloud/CI — they execute the agent frameworks,
# so every `uv sync` command line MUST carry `--extra plugins`.
_CLOUD_SYNC_FILES = (
    ".github/workflows/ci.yml",
    "docker/Dockerfile.manyfold",
    "scripts/release/deploy-cloud.sh",
)

# Files whose `uv sync` runs on the LOCAL lightweight path — they must NOT pull
# the plugins extra (installing the SDKs there would undo the slim-down).
_LOCAL_SYNC_FILES = ("run.sh",)


def _command_lines_with_uv_sync(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue  # skip comments that merely mention `uv sync`
        if "uv sync" in stripped:
            lines.append(stripped)
    return lines


@pytest.mark.parametrize("rel", _CLOUD_SYNC_FILES)
def test_cloud_sync_pulls_plugins_extra(rel):
    lines = _command_lines_with_uv_sync(_REPO / rel)
    assert lines, f"{rel} no longer has a `uv sync` command — update this guard"
    for line in lines:
        assert "--extra plugins" in line, (
            f"{rel}: cloud/CI `uv sync` missing `--extra plugins` → claude_code /"
            f" codex_cli will break there:\n    {line}"
        )


@pytest.mark.parametrize("rel", _LOCAL_SYNC_FILES)
def test_local_sync_stays_light(rel):
    for line in _command_lines_with_uv_sync(_REPO / rel):
        assert "--extra plugins" not in line, (
            f"{rel}: local `uv sync` pulls `--extra plugins`, defeating the "
            f"lightweight build:\n    {line}"
        )
