"""
@file_name: local_sandbox.py
@author: NetMind.AI
@date: 2026-06-25
@description: Local OS sandbox for external IM agent turns (IM identity-tenant, B).

When an external IM subject's turn runs LOCALLY (no cloud executor container), the
agent CLI subprocess is wrapped in an OS sandbox so a prompt-injected Bash can't read
the owner's files / secrets or write outside the visitor's workspace. Cloud turns use
the executor container instead, so this is the no-broker / local path.

Two backends (validated by the 2026-06-25 macOS spike):
- **macOS `sandbox-exec`** — a Seatbelt profile. Uses a BLOCKLIST (`allow default` +
  `deny` the sensitive set): node runs fine and network/mach stay open; we only
  restrict the filesystem. A deny-default allowlist makes node abort (missing
  mach/dyld allows) and Seatbelt is deprecated, so blocklist is the pragmatic choice.
- **Linux `bubblewrap`** — a proper bind-only allowlist (only what's bound is
  visible), which is stronger and robust for node.

CRITICAL: every path put into a profile MUST be canonical (`os.path.realpath`). On
macOS `/tmp` is a symlink to `/private/tmp`; Seatbelt matches the canonical path, so a
non-canonical `(subpath ...)` silently fails to match and the deny is a no-op.

Network is intentionally NOT isolated: the CLI needs it to reach the LLM, and MCP
tools are served over localhost. So this is filesystem isolation; egress filtering is
deferred (see the design doc). This module only BUILDS the wrapped command (pure,
testable); spawning is wired at the agent-loop CLI spawn site.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

# Owner-home subdirectories hidden from an external visitor (credentials / keys).
_SENSITIVE_HOME_SUBDIRS = (
    ".ssh", ".aws", ".config", ".gnupg", ".docker", ".kube", ".netrc",
    ".npmrc", ".pypirc", ".git-credentials",
)

@dataclass(frozen=True)
class SandboxLayout:
    """Paths that define one external turn's sandbox.

    Attributes:
        external_ws: the visitor's workspace — read-write, becomes the cwd.
        owner_ws: the owner's agent workspace — read-only share (None = share nothing).
        base_dir: the workspaces root; sibling subjects live under it and must be hidden.
        sandbox_home: a writable dir the CLI uses as $HOME (so it writes ~/.claude
            state here, never the owner's real home).
        home_dir: the real OS home, whose secret subdirs we hide.
    """

    external_ws: str
    owner_ws: Optional[str]
    base_dir: str
    sandbox_home: str
    home_dir: str


def _canon(p: str) -> str:
    return os.path.realpath(p)


def macos_sandbox_profile(
    layout: SandboxLayout, extra_blocked: Sequence[str] = ()
) -> str:
    """Build a Seatbelt (SBPL) profile for `sandbox-exec -f` (macOS blocklist).

    `allow default` keeps node/network/mach working; we then restrict the filesystem:
    hide sibling subjects (deny the workspaces base, re-allow this run's external ws
    rw + owner ws ro), make the owner share read-only, and hide owner-home secrets.
    All paths are canonicalized (see module note).
    """
    ext = _canon(layout.external_ws)
    base = _canon(layout.base_dir)
    home = _canon(layout.home_dir)

    lines: List[str] = [
        "(version 1)",
        "(allow default)",
        # WRITE confinement: the visitor may write ONLY its own workspace — never the
        # owner's or any other subject's workspace (all live under `base`), nor
        # elsewhere if we extend this. The owner workspace is covered too (it's under
        # base), so no separate owner rule is needed.
        #
        # IMPORTANT — we deny WRITES, not reads, on base. Denying *reads* on base
        # breaks the claude CLI: it stat/traverses the cwd's ancestor dirs up through
        # `base` on startup (project/config discovery), and a read-deny there raises
        # EPERM and the CLI exits 1. So on the macOS blocklist, cross-subject/owner
        # *read* isolation is NOT provided — use Linux bwrap (bind-only allowlist) or
        # the Docker executor for that. Writes + secrets ARE confined here.
        f'(deny file-write* (subpath "{base}"))',
        f'(allow file-write* (subpath "{ext}"))',
    ]
    # Secrets: hide credential / key dirs entirely (read AND write).
    for sub in _SENSITIVE_HOME_SUBDIRS:
        lines.append(f'(deny file* (subpath "{_canon(os.path.join(home, sub))}"))')
    # Caller-supplied extra blocks (e.g. NarraNexus install / DB).
    for b in extra_blocked:
        lines.append(f'(deny file* (subpath "{_canon(b)}"))')
    return "\n".join(lines) + "\n"


def linux_bwrap_argv(
    inner_argv: Sequence[str],
    layout: SandboxLayout,
    extra_blocked: Sequence[str] = (),
) -> List[str]:
    """Build a `bwrap` argv (Linux bind-only allowlist) wrapping `inner_argv`.

    Only bound paths are visible: system runtime read-only, the visitor's workspace
    read-write (same path, set as cwd), the owner share read-only at OWNER_MOUNT, a
    private tmpfs /tmp, and a writable $HOME redirect. Network is NOT unshared (the
    CLI needs the LLM + localhost MCP). `extra_blocked` is unused on Linux — the
    allowlist already hides everything not bound.
    """
    ext = _canon(layout.external_ws)
    home = _canon(layout.sandbox_home)
    argv: List[str] = [
        "bwrap",
        # System runtime (read-only) so node + claude can run.
        "--ro-bind", "/usr", "/usr",
        "--ro-bind-try", "/bin", "/bin",
        "--ro-bind-try", "/sbin", "/sbin",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/opt", "/opt",
        "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        # Visitor workspace: read-write, and the cwd.
        "--bind", ext, ext,
        "--chdir", ext,
        # Redirected HOME so the CLI's ~/.claude state lands in the sandbox.
        "--bind", home, home,
        "--setenv", "HOME", home,
        # PID isolation; network stays shared (no --unshare-net).
        "--unshare-pid",
        "--die-with-parent",
    ]
    if layout.owner_ws:
        # Bind read-only at the SAME path so the owner workspace is at one
        # consistent location across macOS (Seatbelt allows the real path) /
        # Linux / warn-open — the prompt can reference one path everywhere.
        owner = _canon(layout.owner_ws)
        argv += ["--ro-bind", owner, owner]
    argv += ["--", *inner_argv]
    return argv


def detect_local_sandbox() -> Optional[str]:
    """Return the available local sandbox backend ("bwrap" / "sandbox-exec"), or None.

    None → caller falls back to warn-open (run with data isolation only + a loud
    warning; see the design doc decision 1).
    """
    if sys.platform == "darwin":
        return "sandbox-exec" if os.path.exists("/usr/bin/sandbox-exec") else None
    if sys.platform.startswith("linux"):
        return "bwrap" if shutil.which("bwrap") else None
    return None


def build_sandbox_layout(
    agent_id: str,
    subject_user_id: str,
    owner_user_id: Optional[str],
    base: str,
    home_dir: Optional[str] = None,
) -> SandboxLayout:
    """Resolve the on-disk layout for an external turn's sandbox.

    external_ws / owner_ws come from the same workspace_paths layout the runtime
    already uses (so the sandbox binds exactly the real dirs). sandbox_home is a
    `.home` subdir of the visitor workspace (writable; holds the CLI's ~/.claude).
    """
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    external_ws = str(agent_workspace_path(agent_id, subject_user_id, base=base))
    owner_ws = (
        str(agent_workspace_path(agent_id, owner_user_id, base=base))
        if owner_user_id
        else None
    )
    return SandboxLayout(
        external_ws=external_ws,
        owner_ws=owner_ws,
        base_dir=base,
        sandbox_home=os.path.join(external_ws, ".home"),
        home_dir=home_dir or os.path.expanduser("~"),
    )


def prepare_sandbox_wrapper(
    layout: SandboxLayout,
    backend: str,
    real_cli_path: str,
    extra_blocked: Sequence[str] = (),
) -> Tuple[str, Callable[[], None]]:
    """Materialise a wrapper executable that runs `real_cli_path` inside the sandbox.

    Returns ``(wrapper_path, cleanup)``. Point ``ClaudeAgentOptions.cli_path`` at
    wrapper_path: the SDK invokes ``wrapper <its args…>`` and the wrapper re-execs
    the real claude CLI under sandbox-exec (macOS) / bwrap (Linux), forwarding "$@".
    `cleanup()` removes the temp dir; call it after the turn.
    """
    os.makedirs(layout.external_ws, exist_ok=True)
    os.makedirs(layout.sandbox_home, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="nn-sbx-")
    wrapper = os.path.join(tmp, "claude")

    if backend == "sandbox-exec":
        profile_path = os.path.join(tmp, "profile.sb")
        with open(profile_path, "w") as f:
            f.write(macos_sandbox_profile(layout, extra_blocked=extra_blocked))
        inner = (
            f"exec /usr/bin/sandbox-exec -f {shlex.quote(profile_path)} "
            f'{shlex.quote(real_cli_path)} "$@"'
        )
    elif backend == "bwrap":
        # bwrap setup args (everything before the "--" separator).
        prefix = linux_bwrap_argv([], layout, extra_blocked=extra_blocked)[:-1]
        prefix_str = " ".join(shlex.quote(a) for a in prefix)
        inner = f'exec {prefix_str} -- {shlex.quote(real_cli_path)} "$@"'
    else:
        raise ValueError(f"unknown sandbox backend: {backend!r}")

    with open(wrapper, "w") as f:
        f.write(f"#!/bin/sh\n{inner}\n")
    os.chmod(wrapper, 0o755)

    def cleanup() -> None:
        shutil.rmtree(tmp, ignore_errors=True)

    return wrapper, cleanup
