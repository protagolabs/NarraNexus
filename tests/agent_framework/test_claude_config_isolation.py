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
and even survived ``--setting-sources ""``. The fix points the keyed
agent_loop at an isolated ``CLAUDE_CONFIG_DIR`` so the personal settings file
is never read; OAuth keeps the default dir because its credential file lives
there.
"""
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


def test_oauth_keeps_default_config_dir():
    """OAuth reads its credential file from ~/.claude — must stay there."""
    env = ClaudeConfig(api_key="", auth_type="oauth").to_cli_env()
    assert env["CLAUDE_CONFIG_DIR"] == str(Path.home() / ".claude")


def test_config_dir_always_set_to_block_inheritance():
    """The key is always present (complete dict) so a stray parent-process
    CLAUDE_CONFIG_DIR cannot leak in via the SDK's {**os.environ, **env} merge."""
    env = ClaudeConfig(api_key="k").to_cli_env()
    assert "CLAUDE_CONFIG_DIR" in env
    assert env["CLAUDE_CONFIG_DIR"]  # non-empty
