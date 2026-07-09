"""
@file_name: test_claude_config_isolation.py
@author: Bin Liang
@date: 2026-07-08
@description: Guard the agent_loop CLI subprocess against the host user's
personal ``~/.claude/settings.json``.

Incident: on a developer machine whose personal Claude Code config carried an
``env`` block (``ANTHROPIC_BASE_URL``/``ANTHROPIC_AUTH_TOKEN`` pointing at a
private relay), every NarraNexus frontend message failed with
``503 No available accounts``. Root cause: Claude Code applies the
``settings.json`` ``env`` block with HIGHER precedence than the subprocess env
we inject, so it silently overrode the provider config NarraNexus passes in
and even survived ``--setting-sources ""``. The fix points the agent_loop at an
isolated ``CLAUDE_CONFIG_DIR`` so the personal settings file is never read.

2026-07-09 follow-up: OAuth originally kept the real ``~/.claude`` (its
credential file lives there), which re-opened the exact same hole — the
personal ``settings.json`` env block still hijacked OAuth runs, and the
agent_loop raced the user's own Claude Code on ``~/.claude/.claude.json``.
OAuth now gets its OWN isolated dir too; the credential file is staged into it
by ``_stage_claude_oauth_credentials`` (only ``.credentials.json`` is copied —
never ``settings.json``).
"""
import os
from pathlib import Path

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.settings import settings


def test_keyed_auth_isolates_config_dir():
    """bearer_token / api_key providers must NOT read ~/.claude."""
    for auth_type in ("bearer_token", "api_key"):
        env = ClaudeConfig(api_key="k", base_url="https://api.netmind.ai", auth_type=auth_type).to_cli_env()
        assert env["CLAUDE_CONFIG_DIR"] == settings.claude_cli_config_path
        # The whole point: the isolated dir is NOT the host user's personal dir.
        assert Path(env["CLAUDE_CONFIG_DIR"]) != Path.home() / ".claude"
        assert ".nexusagent" in env["CLAUDE_CONFIG_DIR"]


def test_oauth_isolates_config_dir():
    """OAuth no longer shares ~/.claude — it gets its own isolated dir so the
    personal settings.json env block can't hijack it and the agent_loop won't
    race the user's own Claude Code on ~/.claude/.claude.json. The credential
    file is staged in separately (see the staging tests below)."""
    env = ClaudeConfig(api_key="", auth_type="oauth").to_cli_env()
    assert env["CLAUDE_CONFIG_DIR"] == settings.claude_oauth_config_path
    assert Path(env["CLAUDE_CONFIG_DIR"]) != Path.home() / ".claude"
    assert ".nexusagent" in env["CLAUDE_CONFIG_DIR"]
    # keyed and oauth use SEPARATE isolated dirs (oauth carries a staged
    # credential file; keyed injects the key via env instead).
    assert env["CLAUDE_CONFIG_DIR"] != settings.claude_cli_config_path


def test_config_dir_always_set_to_block_inheritance():
    """The key is always present (complete dict) so a stray parent-process
    CLAUDE_CONFIG_DIR cannot leak in via the SDK's {**os.environ, **env} merge."""
    env = ClaudeConfig(api_key="k").to_cli_env()
    assert "CLAUDE_CONFIG_DIR" in env
    assert env["CLAUDE_CONFIG_DIR"]  # non-empty


# =============================================================================
# OAuth credential staging (_stage_claude_oauth_credentials)
# =============================================================================


def test_stage_oauth_credentials_copies_only_credential_file(tmp_path, monkeypatch):
    """Stage ONLY .credentials.json into the isolated dir — never the poisoned
    settings.json that caused the original incident."""
    from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
        _stage_claude_oauth_credentials,
    )

    host = tmp_path / "host_claude"
    host.mkdir()
    (host / ".credentials.json").write_text('{"token":"real"}')
    (host / "settings.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"http://relay"}}'
    )
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(host / ".credentials.json"))

    dest = tmp_path / "isolated"
    _stage_claude_oauth_credentials(dest)

    assert (dest / ".credentials.json").read_text() == '{"token":"real"}'
    # The hijack vector must NOT be carried over.
    assert not (dest / "settings.json").exists()
    # Staged credential must be private (0o600) — the code chmods it, so guard
    # against a regression that drops the permission tightening.
    import stat

    mode = stat.S_IMODE((dest / ".credentials.json").stat().st_mode)
    assert mode == 0o600
    # Atomic stage (temp + os.replace) must not leave a ``.tmp`` turd behind.
    assert not list(dest.glob(".credentials.json.*.tmp"))


def test_stage_oauth_credentials_newest_wins(tmp_path, monkeypatch):
    """A token the CLI refreshed inside the isolated dir must not be clobbered
    by an older host copy (rotating refresh tokens would break otherwise); but
    a fresh host login (host newer) DOES propagate in."""
    from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
        _stage_claude_oauth_credentials,
    )

    host_cred = tmp_path / ".credentials.json"
    host_cred.write_text('{"token":"host_login"}')
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(host_cred))

    dest = tmp_path / "isolated"
    dest.mkdir()
    staged = dest / ".credentials.json"
    staged.write_text('{"token":"cli_refreshed"}')

    base = host_cred.stat().st_mtime
    # Staged copy strictly NEWER than host → refresh preserved.
    os.utime(staged, (base + 10, base + 10))
    _stage_claude_oauth_credentials(dest)
    assert staged.read_text() == '{"token":"cli_refreshed"}'

    # Fresh host login (host now newer) → propagates in.
    os.utime(host_cred, (base + 100, base + 100))
    _stage_claude_oauth_credentials(dest)
    assert staged.read_text() == '{"token":"host_login"}'


def test_stage_oauth_credentials_missing_source_is_noop(tmp_path, monkeypatch):
    """No host credential (never logged in) → warn + no-op, never raise."""
    from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
        _stage_claude_oauth_credentials,
    )

    monkeypatch.setenv(
        "CLAUDE_CLI_CREDENTIALS_PATH", str(tmp_path / "nonexistent.json")
    )
    dest = tmp_path / "isolated"
    _stage_claude_oauth_credentials(dest)  # must not raise
    assert not (dest / ".credentials.json").exists()
