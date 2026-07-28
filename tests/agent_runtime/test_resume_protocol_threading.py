"""
@file_name: test_resume_protocol_threading.py
@author:
@date: 2026-07-28
@description: resume_session_id across the executor boundary (R2) — the
same kwargs ride the disallowed_tools took: step_3 TurnInput.driver_kwargs()
→ RemoteAgentLoopDriver → build_agent_loop_request body → executor_service
unpack → local driver kwarg. Mirrors tests/agent_runtime/
test_executor_seam.py's fake-aiohttp style.

2026-07-28 additions: the resume CAPABILITY is HMAC-authenticated across that
boundary. ``POST /agent-loop`` is unauthenticated by design, but a resume
handle names a CLI transcript in a CLAUDE_CONFIG_DIR shared by all tenants, so
an unvalidated handle + a guessable working_path was a cross-tenant transcript
read. Covered here: token round-trip, tampering with each bound field,
freshness, empty-secret degradation (cold start, request still 200), and that
the compare is constant-time.
"""
from __future__ import annotations

import time

import pytest

# Importing the package registers the local claude_code/codex_cli drivers.
import xyz_agent_context.agent_framework  # noqa: F401
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.loop.remote_driver import (
    RemoteAgentLoopDriver,
)
from xyz_agent_context.agent_runtime import executor_protocol as proto
from xyz_agent_context.agent_runtime.executor_protocol import (
    authorize_resume_session_id,
    build_agent_loop_request,
    sign_resume_token,
    verify_resume_token,
)
from xyz_agent_context.settings import settings

_SECRET = "test-executor-resume-secret"


def _set_minimal_config():
    set_user_config(claude=ClaudeConfig(api_key="k"), openai=OpenAIConfig(), codex=CodexConfig())


@pytest.fixture
def resume_secret(monkeypatch):
    """Provision the resume HMAC secret (what cloud deploy must do)."""
    monkeypatch.setattr(settings, "executor_resume_hmac_secret", _SECRET)
    monkeypatch.setattr(proto, "_resume_secret_warning_emitted", False)
    return _SECRET


@pytest.fixture
def no_resume_secret(monkeypatch):
    """Unprovisioned deploy (and the local/desktop default)."""
    monkeypatch.setattr(settings, "executor_resume_hmac_secret", "")
    monkeypatch.setattr(proto, "_resume_secret_warning_emitted", False)


# ---------------------------------------------------------------------------
# executor_protocol: request body round-trip
# ---------------------------------------------------------------------------

def test_build_request_carries_resume_session_id():
    _set_minimal_config()
    req = build_agent_loop_request(
        framework="claude_code", working_path="/ws/agent_x",
        messages=[{"role": "user", "content": "hi"}],
        mcp_servers={},
        extra_env=None,
        resume_session_id="cli_sess_12345",
    )
    assert req["resume_session_id"] == "cli_sess_12345"
    # Executor-side unpack shape (executor_service reads it exactly so):
    assert (req.get("resume_session_id") or None) == "cli_sess_12345"


def test_build_request_defaults_resume_to_none():
    _set_minimal_config()
    req = build_agent_loop_request(
        framework="claude_code", working_path="/ws/agent_x",
        messages=[], mcp_servers={}, extra_env=None,
    )
    assert req["resume_session_id"] is None
    assert (req.get("resume_session_id") or None) is None


# ---------------------------------------------------------------------------
# remote driver: kwargs → request body (fake aiohttp captures the POST json)
# ---------------------------------------------------------------------------

class _CaptureResp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    @property
    def content(self):
        class _C:
            def iter_any(self):
                async def _gen():
                    yield b'{"event": {"type": "done"}}\n'
                return _gen()
        return _C()


class _CaptureSession:
    captured_body: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None):
        type(self).captured_body = json
        return _CaptureResp()


@pytest.mark.asyncio
async def test_remote_driver_forwards_resume_session_id(monkeypatch):
    import aiohttp

    _set_minimal_config()
    _CaptureSession.captured_body = None
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _CaptureSession())
    monkeypatch.setattr(aiohttp, "ClientTimeout", lambda *a, **k: None)

    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    out = [e async for e in d.agent_loop([], {}, resume_session_id="cli_sess_zzz")]
    assert out == [{"type": "done"}]
    assert _CaptureSession.captured_body["resume_session_id"] == "cli_sess_zzz"


@pytest.mark.asyncio
async def test_remote_driver_omitted_kwarg_sends_none(monkeypatch):
    import aiohttp

    _set_minimal_config()
    _CaptureSession.captured_body = None
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _CaptureSession())
    monkeypatch.setattr(aiohttp, "ClientTimeout", lambda *a, **k: None)

    d = RemoteAgentLoopDriver("claude_code", "/ws", "http://x:8020")
    _ = [e async for e in d.agent_loop([], {})]
    assert _CaptureSession.captured_body["resume_session_id"] is None


# ---------------------------------------------------------------------------
# executor_service: body → local driver kwargs
# ---------------------------------------------------------------------------

class _RecordingDriver:
    """Fake local driver that records the kwargs the executor hands over."""

    last_kwargs: dict | None = None

    def agent_loop(self, messages, mcp_servers, **kwargs):
        type(self).last_kwargs = kwargs

        async def _gen():
            yield {"type": "done"}

        return _gen()


def _executor_client(monkeypatch):
    from fastapi.testclient import TestClient

    from xyz_agent_context.agent_runtime import executor_service

    _RecordingDriver.last_kwargs = None
    monkeypatch.setattr(
        executor_service, "get_agent_loop_driver",
        lambda framework, working_path=None, **kw: _RecordingDriver(),
    )
    return TestClient(executor_service.app)


@pytest.mark.parametrize(
    ("body_value", "expected"),
    [("cli_sess_qqq", "cli_sess_qqq"), (None, None)],
)
def test_executor_service_unpacks_resume_session_id(
    monkeypatch, resume_secret, body_value, expected
):
    """With the secret provisioned, a properly signed body honours resume."""
    client = _executor_client(monkeypatch)
    _set_minimal_config()
    body = build_agent_loop_request(
        framework="claude_code",
        working_path="/ws/a",
        messages=[],
        mcp_servers={},
        extra_env=None,
        resume_session_id=body_value,
    )
    resp = client.post("/agent-loop", json=body)
    assert resp.status_code == 200
    assert _RecordingDriver.last_kwargs is not None
    assert _RecordingDriver.last_kwargs.get("resume_session_id") == expected


# ---------------------------------------------------------------------------
# Resume-capability HMAC: canonical binding, freshness, degradation
# ---------------------------------------------------------------------------

def _signed_body(**overrides) -> dict:
    _set_minimal_config()
    body = build_agent_loop_request(
        framework="claude_code",
        working_path="/ws/victim_user/agent_x",
        messages=[],
        mcp_servers={},
        extra_env=None,
        resume_session_id="cli_sess_secret",
    )
    body.update(overrides)
    return body


def test_build_request_mints_token_when_secret_configured(resume_secret):
    body = _signed_body()
    assert isinstance(body["resume_auth_token"], str) and body["resume_auth_token"]
    assert isinstance(body["resume_auth_issued_at"], int)
    # The secret itself must never appear on the wire.
    assert _SECRET not in repr(body)


def test_build_request_omits_token_without_secret(no_resume_secret):
    body = _signed_body()
    assert body["resume_session_id"] == "cli_sess_secret"
    assert "resume_auth_token" not in body
    assert "resume_auth_issued_at" not in body


def test_token_round_trip_authorizes_resume(resume_secret):
    assert authorize_resume_session_id(_signed_body()) == "cli_sess_secret"


@pytest.mark.parametrize(
    "tamper",
    [
        {"resume_session_id": "cli_sess_of_another_tenant"},
        {"working_path": "/ws/attacker_user/agent_y"},
        {"framework": "codex_cli"},
        {"resume_auth_issued_at": 0},
        {"resume_auth_token": "0" * 64},
    ],
    ids=["session_id", "working_path", "framework", "issued_at", "token"],
)
def test_tampered_field_falls_back_to_cold_start(resume_secret, tamper):
    """Every field inside the canonical string is load-bearing: change one and
    the handle is refused (cold start), never honoured."""
    assert authorize_resume_session_id(_signed_body(**tamper)) is None


def test_absent_token_falls_back_to_cold_start(resume_secret):
    body = _signed_body()
    body.pop("resume_auth_token")
    assert authorize_resume_session_id(body) is None


def test_stale_token_is_rejected(resume_secret):
    """A body captured 10 minutes ago cannot be replayed."""
    stale = int(time.time()) - (proto._RESUME_TOKEN_TTL_SECONDS + 60)
    body = _signed_body(
        resume_auth_issued_at=stale,
        resume_auth_token=sign_resume_token(
            resume_session_id="cli_sess_secret",
            working_path="/ws/victim_user/agent_x",
            framework="claude_code",
            issued_at=stale,
        ),
    )
    assert authorize_resume_session_id(body) is None


def test_future_dated_token_is_rejected(resume_secret):
    future = int(time.time()) + (proto._RESUME_TOKEN_TTL_SECONDS + 60)
    body = _signed_body(
        resume_auth_issued_at=future,
        resume_auth_token=sign_resume_token(
            resume_session_id="cli_sess_secret",
            working_path="/ws/victim_user/agent_x",
            framework="claude_code",
            issued_at=future,
        ),
    )
    assert authorize_resume_session_id(body) is None


def test_verify_uses_constant_time_compare(monkeypatch, resume_secret):
    """Digest comparison must go through hmac.compare_digest — a `==` compare
    leaks the matching prefix length through timing."""
    import hmac as hmac_mod

    seen: list[tuple] = []
    real = hmac_mod.compare_digest

    def _spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(proto.hmac, "compare_digest", _spy)
    issued_at = int(time.time())
    token = sign_resume_token(
        resume_session_id="s", working_path="/w", framework="claude_code",
        issued_at=issued_at,
    )
    assert verify_resume_token(
        token, resume_session_id="s", working_path="/w",
        framework="claude_code", issued_at=issued_at,
    )
    assert len(seen) == 1


def test_no_secret_drops_resume_and_warns_once(no_resume_secret, caplog):
    """Empty secret (local/desktop default, unprovisioned cloud) ⇒ resume is
    ignored entirely and the warning is emitted ONCE, not per turn."""
    body = _signed_body()
    with caplog.at_level("WARNING"):
        assert authorize_resume_session_id(body) is None
        assert authorize_resume_session_id(body) is None
    warnings = [
        r for r in caplog.records if "EXECUTOR_RESUME_HMAC_SECRET" in r.getMessage()
    ]
    assert len(warnings) <= 1  # loguru may not propagate; never more than one
    assert proto._resume_secret_warning_emitted is True


def test_no_secret_request_still_succeeds_cold(monkeypatch, no_resume_secret):
    """Degradation must be invisible to the turn: 200 + cold start, not a 4xx."""
    client = _executor_client(monkeypatch)
    body = _signed_body()
    resp = client.post("/agent-loop", json=body)
    assert resp.status_code == 200
    assert _RecordingDriver.last_kwargs.get("resume_session_id") is None


def test_executor_route_refuses_unsigned_resume(monkeypatch, resume_secret):
    """The attack the HMAC closes: a direct caller who knows a victim's
    working_path + cli_session_id but not the secret gets a cold start."""
    client = _executor_client(monkeypatch)
    resp = client.post("/agent-loop", json={
        "framework": "claude_code",
        "working_path": "/ws/victim_user/agent_x",
        "messages": [],
        "mcp_servers": {},
        "provider_configs": {},
        "resume_session_id": "cli_sess_of_the_victim",
    })
    assert resp.status_code == 200
    assert _RecordingDriver.last_kwargs.get("resume_session_id") is None
