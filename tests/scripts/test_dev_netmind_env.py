"""
@file_name: test_dev_netmind_env.py
@date: 2026-09-09
@description: A source-run local stack (run.sh -> dev-local.sh) uses PROD
NetMind unless the developer asks for protago-dev.

B-40 / upstream NetMindAI-Open/NarraNexus#90: dev-local.sh enabled Power login
against protago-dev by default and exported no VITE_NETMIND_*, so the vite dev
server fell back to accounts.protago-dev.com and "Sign in with GitHub" opened
the dev OAuth app for every source-run user. These tests source the real
scripts/dev/netmind_env.sh in a clean bash.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NETMIND_ENV = REPO / "scripts" / "dev" / "netmind_env.sh"
DEV_LOCAL = REPO / "scripts" / "dev" / "dev-local.sh"


def _resolve(env: dict[str, str]) -> tuple[int, dict[str, str], str]:
    script = (
        f"source '{NETMIND_ENV}' && nexus_netmind_env || exit $?; "
        "nexus_netmind_env_cmd"
    )
    r = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    exported: dict[str, str] = {}
    for part in r.stdout.split("; "):
        part = part.strip()
        if part.startswith("export "):
            key, _, value = part[len("export "):].partition("=")
            exported[key] = value.strip("'")
    return r.returncode, exported, r.stderr


def test_default_is_prod_for_backend_and_frontend():
    rc, env, _ = _resolve({})
    assert rc == 0
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.netmind.ai"
    assert env["VITE_NETMIND_AUTH_API"] == "https://auth-api.netmind.ai"
    assert env["NETMIND_AUTH_API_URL"] == "https://auth-api.netmind.ai"
    assert env["NETMIND_INFERENCE_BASE"] == "https://api.netmind.ai/inference-api"
    assert env["VITE_ENABLE_POWER_LOGIN"] == env["NARRANEXUS_ENABLE_POWER_LOGIN"] == "true"
    assert not any("protago-dev" in v for v in env.values())


def test_dev_opt_in_targets_protago_dev_consistently():
    rc, env, _ = _resolve({"NEXUS_NETMIND_ENV": "dev"})
    assert rc == 0
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.protago-dev.com"
    assert env["NETMIND_AUTH_API_URL"] == env["VITE_NETMIND_AUTH_API"] == (
        "https://userauth.protago-dev.com"
    )


def test_explicit_override_wins():
    rc, env, _ = _resolve({"VITE_NETMIND_ACCOUNTS_URL": "https://accounts.example.netmind.ai"})
    assert rc == 0
    assert env["VITE_NETMIND_ACCOUNTS_URL"] == "https://accounts.example.netmind.ai"


def test_power_login_off_exports_nothing():
    rc, env, _ = _resolve({"NEXUS_DEV_POWER_LOGIN": "0"})
    assert rc == 0
    assert env == {}


def test_unknown_env_aborts():
    rc, _, err = _resolve({"NEXUS_NETMIND_ENV": "staging"})
    assert rc != 0
    assert "NEXUS_NETMIND_ENV" in err


def test_dev_local_forwards_netmind_env_into_every_tmux_window():
    """tmux panes inherit the tmux SERVER's env, not the launcher's: the
    resolved vars must ride in each window command, the frontend's too."""
    text = DEV_LOCAL.read_text()
    assert 'source "$SCRIPT_DIR/netmind_env.sh"' in text
    assert 'NETMIND_ENV="$(nexus_netmind_env_cmd)"' in text
    env_cmd = next(ln for ln in text.splitlines() if ln.startswith('ENV_CMD="'))
    assert "${NETMIND_ENV}" in env_cmd
    frontend = next(ln for ln in text.splitlines() if "Frontend Dev Server" in ln)
    assert "${NETMIND_ENV}" in frontend
    assert "protago-dev.com" not in text  # endpoints live in netmind_env.sh only
