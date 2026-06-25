"""
@file_name: test_local_sandbox.py
@author: NetMind.AI
@date: 2026-06-25
@description: IM identity-tenant (B) — pure builders for the local sandbox command
(macOS Seatbelt profile + Linux bwrap argv). Validated against the 2026-06-25 spike:
macOS uses a BLOCKLIST (allow default + deny sensitive) because node aborts under a
deny-default allowlist; Linux bwrap uses a proper bind-only allowlist. All paths must
be canonical (realpath) or Seatbelt's subpath match silently no-ops.
"""
import os
import subprocess
import sys

import pytest

from xyz_agent_context.agent_framework.local_sandbox import (
    SandboxLayout,
    build_sandbox_layout,
    macos_sandbox_profile,
    linux_bwrap_argv,
    prepare_sandbox_wrapper,
)


def _layout(tmp):
    base = os.path.join(tmp, "workspaces")
    ext = os.path.join(base, "ext_slack_room1", "agent_x")
    owner = os.path.join(base, "owner1", "agent_x")
    for p in (ext, owner):
        os.makedirs(p, exist_ok=True)
    return SandboxLayout(
        external_ws=ext,
        owner_ws=owner,
        base_dir=base,
        sandbox_home=os.path.join(ext, ".home"),
        home_dir=tmp,
    )


# ---- macOS Seatbelt profile (blocklist) ----------------------------------

def test_macos_profile_is_blocklist(tmp_path):
    p = macos_sandbox_profile(_layout(str(tmp_path)))
    assert p.startswith("(version 1)")
    assert "(allow default)" in p  # blocklist: node/network/mach all work


def test_macos_profile_uses_canonical_paths(tmp_path):
    lay = _layout(str(tmp_path))
    p = macos_sandbox_profile(lay)
    # The canonical (realpath) external ws must appear — not a /tmp-symlinked form.
    assert os.path.realpath(lay.external_ws) in p


def test_macos_profile_owner_is_read_only(tmp_path):
    lay = _layout(str(tmp_path))
    p = macos_sandbox_profile(lay)
    owner = os.path.realpath(lay.owner_ws)
    assert f'(allow file-read* (subpath "{owner}"))' in p
    assert f'(deny file-write* (subpath "{owner}"))' in p


def test_macos_profile_isolates_sibling_subjects(tmp_path):
    lay = _layout(str(tmp_path))
    p = macos_sandbox_profile(lay)
    base = os.path.realpath(lay.base_dir)
    ext = os.path.realpath(lay.external_ws)
    # base denied (hides sibling subjects), this run's external ws re-allowed rw.
    assert f'(deny file-read* (subpath "{base}"))' in p
    assert f'(allow file* (subpath "{ext}"))' in p


def test_macos_profile_blocks_home_secrets(tmp_path):
    lay = _layout(str(tmp_path))
    p = macos_sandbox_profile(lay)
    ssh = os.path.realpath(os.path.join(lay.home_dir, ".ssh"))
    assert f'(deny file* (subpath "{ssh}"))' in p


def test_macos_profile_does_not_block_network(tmp_path):
    # Network must stay open (LLM + localhost MCP). No deny on network/mach.
    p = macos_sandbox_profile(_layout(str(tmp_path)))
    assert "(deny network" not in p


def test_macos_profile_extra_blocked(tmp_path):
    lay = _layout(str(tmp_path))
    secret = str(tmp_path / "narranexus_db")
    p = macos_sandbox_profile(lay, extra_blocked=[secret])
    assert os.path.realpath(secret) in p


# ---- Linux bwrap argv (allowlist) ----------------------------------------

def test_bwrap_binds_external_rw_and_owner_ro(tmp_path):
    lay = _layout(str(tmp_path))
    argv = linux_bwrap_argv(["claude", "-p"], lay)
    s = " ".join(argv)
    assert argv[0] == "bwrap"
    ext = os.path.realpath(lay.external_ws)
    owner = os.path.realpath(lay.owner_ws)
    assert f"--bind {ext} {ext}" in s          # external rw
    assert f"--ro-bind {owner} {owner}" in s    # owner ro at its real path
    assert "--tmpfs /tmp" in s
    assert "--setenv HOME" in s


def test_bwrap_keeps_network(tmp_path):
    argv = linux_bwrap_argv(["claude"], _layout(str(tmp_path)))
    # Network shared → must NOT unshare net.
    assert "--unshare-net" not in argv


def test_bwrap_inner_command_after_separator(tmp_path):
    argv = linux_bwrap_argv(["claude", "-p", "hi"], _layout(str(tmp_path)))
    i = argv.index("--")
    assert argv[i + 1:] == ["claude", "-p", "hi"]


# ---- layout resolution ----------------------------------------------------

def test_build_sandbox_layout(tmp_path):
    base = str(tmp_path / "ws")
    lay = build_sandbox_layout("agent_x", "ext:slack:room1", "owner1", base, home_dir="/home/o")
    assert lay.external_ws.endswith("/agent_x") and "ext:slack:room1" in lay.external_ws
    assert lay.owner_ws.endswith("/agent_x") and "owner1" in lay.owner_ws
    assert lay.sandbox_home == os.path.join(lay.external_ws, ".home")
    assert lay.home_dir == "/home/o"


def test_build_sandbox_layout_no_owner(tmp_path):
    lay = build_sandbox_layout("agent_x", "ext:slack:room1", None, str(tmp_path))
    assert lay.owner_ws is None


# ---- wrapper generation ---------------------------------------------------

def test_prepare_wrapper_macos_structure(tmp_path):
    lay = _layout(str(tmp_path))
    wrapper, cleanup = prepare_sandbox_wrapper(lay, "sandbox-exec", "/usr/local/bin/claude")
    try:
        assert os.access(wrapper, os.X_OK)
        body = open(wrapper).read()
        assert "sandbox-exec -f" in body
        assert "/usr/local/bin/claude" in body
        assert body.rstrip().endswith('"$@"')
    finally:
        cleanup()
    assert not os.path.exists(wrapper)  # cleanup removed it


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_prepare_wrapper_macos_enforces(tmp_path):
    """End-to-end: the generated wrapper actually sandboxes the (fake) CLI."""
    lay = _layout(str(tmp_path))
    # A fake "claude" that probes the filesystem and prints outcomes.
    fake = str(tmp_path / "fakecli.sh")
    secret = str(tmp_path / "secret")
    os.makedirs(secret, exist_ok=True)
    open(os.path.join(secret, "k.txt"), "w").write("SECRET")
    with open(fake, "w") as f:
        f.write(
            "#!/bin/sh\n"
            f"echo x > {lay.external_ws}/w.txt && echo EXT_WRITE_OK\n"
            f"(echo x > {lay.owner_ws}/h.txt) 2>/dev/null && echo OWNER_WRITE_BAD || echo OWNER_WRITE_BLOCKED\n"
            f"cat {secret}/k.txt 2>/dev/null && echo SECRET_BAD || echo SECRET_BLOCKED\n"
        )
    os.chmod(fake, 0o755)
    wrapper, cleanup = prepare_sandbox_wrapper(
        lay, "sandbox-exec", fake, extra_blocked=[secret]
    )
    try:
        out = subprocess.run([wrapper], capture_output=True, text=True).stdout
    finally:
        cleanup()
    assert "EXT_WRITE_OK" in out
    assert "OWNER_WRITE_BLOCKED" in out
    assert "SECRET_BLOCKED" in out
