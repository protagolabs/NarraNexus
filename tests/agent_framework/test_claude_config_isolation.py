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
import json
import os
from pathlib import Path

from narranexus.platform.agent_framework.api_config import ClaudeConfig
from narranexus.platform.settings import settings


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
    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    host = tmp_path / "host_claude"
    host.mkdir()
    (host / ".credentials.json").write_text('{"token":"real"}')
    (host / "settings.json").write_text(
        '{"env":{"ANTHROPIC_BASE_URL":"http://relay"}}'
    )
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(host / ".credentials.json"))
    # Neutralize the real macOS Keychain so this exercises the host-file path
    # deterministically on a dev Mac (where the Keychain otherwise wins).
    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: None)

    dest = tmp_path / "isolated"
    sdk._stage_claude_oauth_credentials(dest)

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
    """Host-file path (Linux/cloud, or macOS without a Keychain entry): a token
    the CLI refreshed inside the isolated dir must not be clobbered by an older
    host copy (rotating refresh tokens would break); a fresh host login (host
    newer by mtime) DOES propagate in."""
    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    host_cred = tmp_path / ".credentials.json"
    host_cred.write_text('{"token":"host_login"}')
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(host_cred))
    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: None)  # force file path

    dest = tmp_path / "isolated"
    dest.mkdir()
    staged = dest / ".credentials.json"
    staged.write_text('{"token":"cli_refreshed"}')

    base = host_cred.stat().st_mtime
    # Staged copy strictly NEWER than host → refresh preserved.
    os.utime(staged, (base + 10, base + 10))
    sdk._stage_claude_oauth_credentials(dest)
    assert staged.read_text() == '{"token":"cli_refreshed"}'

    # Fresh host login (host now newer) → propagates in.
    os.utime(host_cred, (base + 100, base + 100))
    sdk._stage_claude_oauth_credentials(dest)
    assert staged.read_text() == '{"token":"host_login"}'


def test_stage_oauth_credentials_missing_source_is_noop(tmp_path, monkeypatch):
    """No host file AND no Keychain entry → warn + no-op, never raise."""
    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    monkeypatch.setenv(
        "CLAUDE_CLI_CREDENTIALS_PATH", str(tmp_path / "nonexistent.json")
    )
    # Force the macOS Keychain to report "no entry" so this is a true no-op on
    # EVERY platform — a dev Mac's real Keychain may hold a token.
    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: None)
    dest = tmp_path / "isolated"
    sdk._stage_claude_oauth_credentials(dest)  # must not raise
    assert not (dest / ".credentials.json").exists()


def test_darwin_keychain_wins_over_stale_host_file(tmp_path, monkeypatch):
    """Regression (2026-07-12): on macOS a STALE ``~/.claude/.credentials.json``
    must NOT shadow a freshly-logged-in Keychain token. The old code preferred
    the host file whenever it existed, pinning the isolated dir to an expired
    Jun-25 relic → the isolated CLI reported 'Not logged in' every turn."""
    import sys

    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    stale_host = tmp_path / ".credentials.json"
    stale_host.write_text('{"claudeAiOauth":{"accessToken":"STALE","expiresAt":1000}}')
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(stale_host))
    monkeypatch.setattr(sys, "platform", "darwin")

    fresh = '{"claudeAiOauth":{"accessToken":"FRESH","expiresAt":2000}}'
    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: fresh)

    dest = tmp_path / "isolated"
    sdk._stage_claude_oauth_credentials(dest)
    staged = json.loads((dest / ".credentials.json").read_text())
    assert staged["claudeAiOauth"]["accessToken"] == "FRESH"


def test_darwin_falls_back_to_host_file_when_keychain_empty(tmp_path, monkeypatch):
    """macOS legacy CLI: no Keychain entry but a host file exists → stage the
    host file (the file path), so old file-based logins still work."""
    import sys

    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    host = tmp_path / ".credentials.json"
    host.write_text('{"token":"from_file"}')
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(host))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: None)  # Keychain empty

    dest = tmp_path / "isolated"
    sdk._stage_claude_oauth_credentials(dest)
    assert (dest / ".credentials.json").read_text() == '{"token":"from_file"}'


def test_stage_blob_newest_wins_restages_when_newer(tmp_path):
    """A source blob with a LATER ``expiresAt`` replaces a stale staged copy —
    this is what propagates a fresh ``claude login`` on macOS."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _stage_blob_newest_wins,
    )

    dest = tmp_path / "isolated"
    dest.mkdir()
    staged = dest / ".credentials.json"
    staged.write_text('{"claudeAiOauth":{"accessToken":"stale","expiresAt":1000}}')

    fresh = '{"claudeAiOauth":{"accessToken":"fresh","expiresAt":2000}}'
    _stage_blob_newest_wins(dest, fresh, sourced_from="test")
    assert json.loads(staged.read_text())["claudeAiOauth"]["accessToken"] == "fresh"


def test_stage_blob_preserves_inplace_refresh(tmp_path):
    """A token the isolated CLI refreshed in place (staged ``expiresAt`` NEWER
    than the source's stale copy) must NOT be clobbered — re-injecting the
    source's already-consumed refresh token would log the user out."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _stage_blob_newest_wins,
    )

    dest = tmp_path / "isolated"
    dest.mkdir()
    staged = dest / ".credentials.json"
    staged.write_text('{"claudeAiOauth":{"accessToken":"refreshed","expiresAt":5000}}')

    stale = '{"claudeAiOauth":{"accessToken":"src_old","expiresAt":1000}}'
    _stage_blob_newest_wins(dest, stale, sourced_from="test")
    assert (
        json.loads(staged.read_text())["claudeAiOauth"]["accessToken"] == "refreshed"
    )


# =============================================================================
# One-shot macOS Keychain import (GitHub #117): stale isolated CONFIG_DIR
# state must be cleared before staging a genuinely rotated host credential,
# or the isolated CLI keeps reading its own frozen one-shot import forever.
# =============================================================================


def test_should_reset_when_no_existing_stage():
    """Nothing staged yet — first stage ever, nothing stale to clear."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _should_reset_isolated_config_dir,
    )

    fresh = '{"claudeAiOauth":{"expiresAt":2000}}'
    assert _should_reset_isolated_config_dir(None, fresh) is False


def test_should_reset_true_on_genuine_rotation():
    """Source strictly newer than the staged copy -> a real claude login
    happened; the isolated CLI's frozen one-shot import must be cleared."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _should_reset_isolated_config_dir,
    )

    old = '{"claudeAiOauth":{"expiresAt":1000}}'
    new = '{"claudeAiOauth":{"expiresAt":2000}}'
    assert _should_reset_isolated_config_dir(old, new) is True


def test_should_reset_false_when_not_newer():
    """Source is the SAME or OLDER than what's already staged — no rotation
    happened, so don't churn the isolated dir on every spawn."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _should_reset_isolated_config_dir,
    )

    same = '{"claudeAiOauth":{"expiresAt":2000}}'
    older = '{"claudeAiOauth":{"expiresAt":1000}}'
    assert _should_reset_isolated_config_dir(same, same) is False
    assert _should_reset_isolated_config_dir(same, older) is False


def test_should_reset_false_when_new_blob_unparseable():
    """An unparseable source must never trigger a reset — matches
    _stage_blob_newest_wins's own 'never clobber a good file' rule."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _should_reset_isolated_config_dir,
    )

    old = '{"claudeAiOauth":{"expiresAt":1000}}'
    garbage = "not json at all"
    assert _should_reset_isolated_config_dir(old, garbage) is False


def test_should_reset_true_when_existing_blob_unparseable():
    """A corrupt/unreadable staged copy must be cleared so the CLI gets a
    genuinely clean re-import rather than inheriting corrupt state."""
    from narranexus_plugins.frameworks_claude_code.sdk import (
        _should_reset_isolated_config_dir,
    )

    corrupt = "not json at all"
    fresh = '{"claudeAiOauth":{"expiresAt":2000}}'
    assert _should_reset_isolated_config_dir(corrupt, fresh) is True


def test_stage_darwin_wipes_stale_config_dir_state_on_rotation(tmp_path, monkeypatch):
    """End-to-end: an isolated dir carrying leftover CLI state (simulating its
    own one-shot Keychain-import bookkeeping) from a PREVIOUS session must be
    wiped before staging a genuinely rotated Keychain credential — the stale
    extra file must not survive."""
    import sys

    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    monkeypatch.setattr(sys, "platform", "darwin")

    dest = tmp_path / "isolated"
    dest.mkdir()
    (dest / ".credentials.json").write_text(
        '{"claudeAiOauth":{"accessToken":"OLD","expiresAt":1000}}'
    )
    # Simulate CLI-internal state from the CONFIG_DIR's first one-shot import
    # (the real culprit is a macOS Keychain entry we cannot inspect here;
    # this stand-in proves the wipe actually clears the directory).
    (dest / ".claude.json").write_text('{"stale":"cli-internal-state"}')

    fresh = '{"claudeAiOauth":{"accessToken":"NEW","expiresAt":2000}}'
    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: fresh)

    sdk._stage_claude_oauth_credentials(dest)

    assert not (dest / ".claude.json").exists()  # wiped
    staged = json.loads((dest / ".credentials.json").read_text())
    assert staged["claudeAiOauth"]["accessToken"] == "NEW"


def test_stage_darwin_keeps_config_dir_when_keychain_not_rotated(tmp_path, monkeypatch):
    """No rotation (Keychain blob unchanged) -> the isolated dir's other
    state must be left alone; only newest-wins staging logic applies."""
    import sys

    from narranexus_plugins.frameworks_claude_code import sdk as sdk

    monkeypatch.setattr(sys, "platform", "darwin")

    dest = tmp_path / "isolated"
    dest.mkdir()
    blob = '{"claudeAiOauth":{"accessToken":"SAME","expiresAt":2000}}'
    (dest / ".credentials.json").write_text(blob)
    (dest / ".claude.json").write_text('{"kept":"cli-internal-state"}')

    monkeypatch.setattr(sdk, "_read_keychain_blob", lambda: blob)

    sdk._stage_claude_oauth_credentials(dest)

    assert (dest / ".claude.json").exists()  # untouched — no rotation happened
