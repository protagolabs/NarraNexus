"""
@file_name: _subprocess.py
@author: Bin Liang
@date: 2026-09-03
@description: Run a probe in a fresh interpreter with a controlled environment and return its JSON.

Approval snapshots must not depend on what earlier tests imported or on the
developer's ambient environment (a built frontend, a cloud-ish .env, an
already-imported driver package). A subprocess with an explicit environment is
the only way to make "the route set / the registry contents at import" a
deterministic function of the code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]

# Every variable that can change what the app registers at import time.
_SCRUBBED = (
    "NARRANEXUS_DEPLOYMENT_MODE",
    "DATABASE_URL",
    "DB_HOST",
    "ENABLE_MANYFOLD_API",
    "FRONTEND_DIST",
    "AGENT_EXECUTOR_URL",
    "NARRA_SURFACE",
    "NEXUS_DIAG_ENV",
)


def run_probe(code: str, *, env: Mapping[str, str]) -> Any:
    """Execute ``code`` (which prints one JSON line last) under ``env`` on top of a scrubbed base."""
    base = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
    base["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    base.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        env=base,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise AssertionError(f"probe failed (rc={proc.returncode}):\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])
