"""
@file_name: deps.py
@author: Bin Liang
@date: 2026-09-03
@description: Install a plugin's pip dependencies into its private directory — wheels only, no build scripts, bounded time, allow-listed indexes.

``uv pip install --target`` when ``uv`` is on the PATH, else ``python -m
pip install --target``; both with ``--only-binary=:all:`` (no sdist builds,
so no ``setup.py`` runs), ``--no-deps`` NOT set (dependencies resolve), a
120 s deadline, and the index restricted to PyPI plus what the manifest
declared. The subprocess runner is injectable so tests never hit the
network or a package manager.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from narranexus.contracts import PluginError

DEFAULT_TIMEOUT_S = 120.0
PYPI = "https://pypi.org/simple"


class DepsError(PluginError):
    pass


@dataclass(frozen=True)
class DepsResult:
    target: Path
    requirements: tuple[str, ...]
    command: tuple[str, ...]
    output_tail: str


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _default_runner(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd), capture_output=True, text=True, timeout=timeout, env={"PIP_NO_INPUT": "1", "PATH": _path()}, check=False
    )


def _path() -> str:
    import os

    return os.environ.get("PATH", "")


def build_command(requirements: Sequence[str], target: Path, *, indexes: Sequence[str] = ()) -> tuple[str, ...]:
    uv = shutil.which("uv")
    base: list[str] = [uv, "pip", "install", "--python", sys.executable] if uv else [sys.executable, "-m", "pip", "install"]
    cmd = base + ["--target", str(target), "--only-binary=:all:", "--index-url", PYPI]
    for extra in indexes:
        if extra != PYPI:
            cmd += ["--extra-index-url", extra]
    cmd += list(requirements)
    return tuple(cmd)


def install_deps(
    requirements: Sequence[str],
    target: Path,
    *,
    indexes: Sequence[str] = (),
    timeout_s: float = DEFAULT_TIMEOUT_S,
    runner: Runner | None = None,
) -> DepsResult | None:
    """Install ``requirements`` into ``target``. Returns ``None`` when there is nothing to install."""
    reqs = tuple(r.strip() for r in requirements if r.strip())
    if not reqs:
        return None
    for r in reqs:
        if r.startswith("-") or any(ch in r for ch in " ;"):
            raise DepsError(f"refusing requirement {r!r}: options and spaces are not allowed")
    for idx in indexes:
        if not idx.startswith("https://"):
            raise DepsError(f"index {idx!r} must be https")
    target.mkdir(parents=True, exist_ok=True)
    cmd = build_command(reqs, target, indexes=indexes)
    run = runner or _default_runner
    try:
        proc = run(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        raise DepsError(f"dependency install exceeded {timeout_s:.0f}s") from None
    except OSError as exc:
        raise DepsError(f"cannot run the package manager: {exc}") from exc
    tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
    if proc.returncode != 0:
        raise DepsError(f"dependency install failed (rc={proc.returncode}): {tail.strip()[-800:]}")
    return DepsResult(target=target, requirements=reqs, command=cmd, output_tail=tail)


__all__ = ["DEFAULT_TIMEOUT_S", "DepsError", "DepsResult", "PYPI", "Runner", "build_command", "install_deps"]
