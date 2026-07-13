"""
@file_name: npm_cli.py
@author: rujing.yan
@date: 2026-07-13
@description: Generic resolver for globally-installed npm CLI binaries.

A login shell has npm's global bin + the node bin on PATH; a process spawned by
a Docker CMD, a GUI launcher (launchd), or our MCP runner frequently does NOT —
its PATH is stripped to something like ``/usr/bin:/bin``. That makes both the
``<cli>`` binary AND its ``#!/usr/bin/env node`` shebang invisible, so the spawn
fails with ENOENT even though the CLI runs fine in the user's terminal (the
exact class of bug lark-cli hit in issue #53).

This module generalises lark_cli_client's private ``_resolve_lark_cli`` /
``_discover_node_bin_dirs`` so any npm-global CLI (e.g. ``officecli``) can reuse
the same PATH-augmentation strategy without cross-importing another Module's
private impl (binding rule #3). lark keeps its own copy for now; new callers use
this one.

Public:
- discover_node_bin_dirs() -> tuple[str, ...]
- resolve_npm_cli(name, env_override=None) -> tuple[str, tuple[str, ...]]
"""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path
from typing import Optional


def discover_node_bin_dirs() -> tuple[str, ...]:
    """Best-effort list of dirs holding npm-global bins + the node binary.

    The directory that contains ``node`` / ``npm`` is exactly where
    ``npm install -g`` drops its bin symlinks (true for vanilla, Homebrew, nvm
    and n), so resolving those tools also locates any npm-global CLI and
    satisfies its ``env node`` shebang. Static fallbacks + version-manager globs
    cover hosts where even node/npm are off this process's PATH.
    """
    dirs: list[str] = []
    for tool in ("npm", "node"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).resolve().parent))

    home = Path.home()
    dirs += [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        str(home / ".npm-global" / "bin"),
        str(home / ".npm-packages" / "bin"),
    ]
    # Version managers install node under a per-version dir that is rarely on a
    # stripped PATH; glob every installed version's bin.
    dirs += sorted(glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "bin")))
    dirs += sorted(glob.glob("/usr/local/n/versions/node/*/bin"))

    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return tuple(out)


def resolve_npm_cli(
    name: str, env_override: Optional[str] = None
) -> tuple[str, tuple[str, ...]]:
    """Resolve an npm-global CLI to ``(executable, extra_PATH_dirs)``.

    Resolution order: explicit ``env_override`` env var (absolute, executable)
    → current PATH → an augmented PATH built from :func:`discover_node_bin_dirs`.
    When nothing resolves we return the bare ``name`` so the caller still gets a
    clear ENOENT (and a retry after the user installs the CLI mid-session
    re-discovers it). Unlike lark's memoised resolver this one is stateless —
    the discovery cost is a handful of ``os.path.isdir`` calls, negligible next
    to spawning a subprocess.

    Args:
        name: the CLI binary name, e.g. ``"officecli"``.
        env_override: optional env-var name holding an absolute path override,
            e.g. ``"OFFICECLI_BIN"``.

    Returns:
        (resolved_executable, extra_path_dirs) — prepend ``extra_path_dirs`` to
        the child process' PATH so its ``env node`` shebang resolves too.
    """
    extra = discover_node_bin_dirs()

    override = os.environ.get(env_override) if env_override else None
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override, extra

    resolved = shutil.which(name) or shutil.which(
        name, path=os.pathsep.join([*extra, os.environ.get("PATH", "")])
    )
    return (resolved or name), extra
