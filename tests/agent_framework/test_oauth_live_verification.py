"""
@file_name: test_oauth_live_verification.py
@author: NarraNexus
@date: 2026-07-31
@description: The Test button must not lie about host-CLI OAuth credentials.

P0 bug: ``user_service.test_provider`` answered ``auth_type == "oauth"``
with an unconditional ``True, "OAuth provider (managed by Claude Code
CLI)"`` — expired codex (or claude) CLI credentials still showed
"usable" and passed the connection test, and ``ProviderReadiness``
(which delegates here) happily re-armed paused jobs onto dead
credentials. Same defect class as the 2026-07-23 oauth_token incident,
fixed then for oauth_token only.

Contract pinned here:
- oauth rows delegate to the driver's ``verify_live`` — a real one-shot
  through the same CLI transport the agent uses. No unconditional pass.
- codex rows resolve the codex driver even when driver_type is missing
  (legacy rows): protocol openai → codex_oauth, else claude_oauth.
- ``CodexOAuthDriver.verify_live`` fails fast (no CLI spawn) when the
  credentials file is missing or the codex binary is absent; reports the
  CLI's terminal error event (e.g. unauthorized) as a failure; and only
  reports success when the CLI actually replied.
- ``ClaudeOAuthDriver.verify_live`` (host-oauth mode) fails fast when
  the host credential store has nothing to verify.
- ``registry.test_provider`` (transient-config path, api-key onboarding
  only) fails CLOSED for oauth configs instead of failing open.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from xyz_agent_context.agent_framework.providers.driver.base import ProviderCard
from xyz_agent_context.agent_framework.providers.driver.drivers.codex_oauth import (
    CodexOAuthDriver,
)
from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
    ClaudeOAuthDriver,
)


def _codex_card(**overrides) -> ProviderCard:
    base = dict(
        provider_id="prov_codex",
        user_id="user_x",
        name="Codex CLI",
        source="user",
        protocol="openai",
        auth_type="oauth",
        api_key="",
        base_url="",
        models=["gpt-5.1-codex"],
        driver_type="codex_oauth",
        auth_ref="codex-cli:~/.codex/auth.json",
    )
    base.update(overrides)
    return ProviderCard(**base)


# ---------------------------------------------------------------------------
# CodexOAuthDriver.verify_live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codex_verify_live_fails_fast_without_credentials(monkeypatch, tmp_path):
    """Missing auth.json → immediate failure, no CLI spawn."""
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(
        mod, "resolve_codex_credentials_path", lambda ref: tmp_path / "absent.json"
    )
    spawned = []
    driver = CodexOAuthDriver(_codex_card())
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.get_agent_loop_driver",
        lambda **kw: spawned.append(kw),
    )

    ok, msg = await driver.verify_live()

    assert ok is False
    assert "not found" in msg
    assert spawned == []


@pytest.mark.asyncio
async def test_codex_verify_live_fails_fast_without_cli(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(mod, "resolve_codex_credentials_path", lambda ref: auth)
    monkeypatch.setattr("shutil.which", lambda name: None)

    ok, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert ok is False
    assert "codex CLI not found" in msg


class _FakeDriver:
    """Stands in for the codex agent-loop driver: replays canned events."""

    def __init__(self, events):
        self._events = events

    def agent_loop(self, **kwargs):
        async def _gen():
            for ev in self._events:
                yield ev

        return _gen()


def _wire_codex_oneshot(monkeypatch, tmp_path, events):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(mod, "resolve_codex_credentials_path", lambda ref: auth)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/codex")

    import xyz_agent_context.agent_framework as fw

    monkeypatch.setattr(
        fw, "get_agent_loop_driver", lambda **kw: _FakeDriver(events)
    )


@pytest.mark.asyncio
async def test_codex_verify_live_reports_unauthorized(monkeypatch, tmp_path):
    """The codex CLI surfaces dead credentials as a terminal error EVENT —
    exactly the case the old unconditional True papered over."""
    _wire_codex_oneshot(
        monkeypatch,
        tmp_path,
        [
            {
                "type": "raw_response_event",
                "data": {
                    "type": "response.error",
                    "error_type": "unauthorized",
                    "error_message": "access token could not be refreshed",
                },
            }
        ],
    )

    ok, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert ok is False
    assert "unauthorized" in msg


@pytest.mark.asyncio
async def test_codex_verify_live_succeeds_on_real_reply(monkeypatch, tmp_path):
    _wire_codex_oneshot(
        monkeypatch,
        tmp_path,
        [
            {
                "type": "raw_response_event",
                "data": {"type": "response.text.delta", "delta": "OK"},
            },
            {"type": "raw_response_event", "data": {"type": "response.done"}},
        ],
    )

    ok, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert ok is True
    assert "live" in msg.lower()


@pytest.mark.asyncio
async def test_codex_verify_live_silence_is_failure(monkeypatch, tmp_path):
    """A run that produces neither text nor an error event must not pass."""
    _wire_codex_oneshot(
        monkeypatch,
        tmp_path,
        [{"type": "raw_response_event", "data": {"type": "response.done"}}],
    )

    ok, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert ok is False


# ---------------------------------------------------------------------------
# ClaudeOAuthDriver.verify_live — host-oauth mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_host_oauth_verify_live_fails_fast_without_credentials(
    monkeypatch, tmp_path
):
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        claude_oauth as mod,
    )

    monkeypatch.setattr(
        mod, "resolve_claude_credentials_path", lambda ref: tmp_path / "absent.json"
    )
    monkeypatch.setattr(
        ClaudeOAuthDriver, "_keychain_has_credentials", staticmethod(_no_keychain)
    )

    card = ProviderCard(
        provider_id="prov_claude",
        user_id="user_x",
        name="Claude Code Login",
        source="user",
        protocol="anthropic",
        auth_type="oauth",
        api_key="",
        base_url="",
        models=["opus"],
        driver_type="claude_oauth",
        auth_ref="claude-cli:~/.claude/.credentials.json",
    )
    ok, msg = await ClaudeOAuthDriver(card).verify_live()

    assert ok is False
    assert "not found" in msg


async def _no_keychain() -> bool:
    return False


# ---------------------------------------------------------------------------
# user_service.test_provider — oauth rows delegate, never uncondition-pass
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_client():
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


async def _seed_oauth_row(db, *, protocol="openai", driver_type="codex_oauth"):
    await db.insert("user_providers", {
        "user_id": "user_x",
        "provider_id": "prov_oauth",
        "name": "CLI login",
        "source": "user",
        "protocol": protocol,
        "auth_type": "oauth",
        "api_key": "",
        "base_url": "",
        "models": '["gpt-5.1-codex"]',
        "driver_type": driver_type,
        "auth_ref": "codex-cli:~/.codex/auth.json",
    })


@pytest.mark.asyncio
async def test_oauth_row_delegates_to_driver_verify_live(db_client, monkeypatch):
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    calls = []

    async def fake_verify(self):
        calls.append(type(self).__name__)
        return False, "access token could not be refreshed — run `codex login`"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", fake_verify)

    await _seed_oauth_row(db_client)
    ok, msg = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is False
    assert "codex login" in msg
    assert calls == ["CodexOAuthDriver"]


@pytest.mark.asyncio
async def test_oauth_row_without_driver_type_resolves_by_protocol(db_client, monkeypatch):
    """Legacy rows predate the driver_type column: openai → codex_oauth."""
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    calls = []

    async def fake_verify(self):
        calls.append(type(self).__name__)
        return True, "live"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", fake_verify)

    await _seed_oauth_row(db_client, driver_type=None)
    ok, _ = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is True
    assert calls == ["CodexOAuthDriver"]


@pytest.mark.asyncio
async def test_oauth_row_never_passes_unconditionally(db_client, monkeypatch):
    """The literal regression: a dead credential must not test green."""
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    async def dead(self):
        return False, "expired"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", dead)

    await _seed_oauth_row(db_client)
    ok, msg = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is False
    assert "managed by Claude Code CLI" not in msg


# ---------------------------------------------------------------------------
# registry.test_provider — transient path fails closed for oauth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_test_provider_fails_closed_for_oauth():
    from xyz_agent_context.agent_framework.providers.registry import (
        ProviderRegistry,
    )
    from xyz_agent_context.schema.provider_schema import (
        AuthType,
        ProviderConfig,
        ProviderProtocol,
        ProviderSource,
    )

    cfg = ProviderConfig(
        provider_id="_transient",
        name="oauth transient",
        source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.OAUTH,
        api_key="",
        base_url="",
        models=[],
    )
    ok, msg = await ProviderRegistry().test_provider(cfg)

    assert ok is False
