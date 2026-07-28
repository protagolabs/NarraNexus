"""
@file_name: test_resume_protocol_threading.py
@author:
@date: 2026-07-28
@description: resume_session_id across the executor boundary (R2) — the
same kwargs ride the disallowed_tools took: step_3 TurnInput.driver_kwargs()
→ RemoteAgentLoopDriver → build_agent_loop_request body → executor_service
unpack → local driver kwarg. Mirrors tests/agent_runtime/
test_executor_seam.py's fake-aiohttp style.
"""
from __future__ import annotations

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
from xyz_agent_context.agent_runtime.executor_protocol import (
    build_agent_loop_request,
)


def _set_minimal_config():
    set_user_config(claude=ClaudeConfig(api_key="k"), openai=OpenAIConfig(), codex=CodexConfig())


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


@pytest.mark.parametrize(
    ("body_value", "expected"),
    [("cli_sess_qqq", "cli_sess_qqq"), (None, None)],
)
def test_executor_service_unpacks_resume_session_id(monkeypatch, body_value, expected):
    from fastapi.testclient import TestClient

    from xyz_agent_context.agent_runtime import executor_service

    _RecordingDriver.last_kwargs = None
    monkeypatch.setattr(
        executor_service, "get_agent_loop_driver",
        lambda framework, working_path=None, **kw: _RecordingDriver(),
    )

    client = TestClient(executor_service.app)
    body = {
        "framework": "claude_code",
        "working_path": "/ws/a",
        "messages": [],
        "mcp_servers": {},
        "provider_configs": {},
    }
    if body_value is not None:
        body["resume_session_id"] = body_value
    resp = client.post("/agent-loop", json=body)
    assert resp.status_code == 200
    assert _RecordingDriver.last_kwargs is not None
    assert _RecordingDriver.last_kwargs.get("resume_session_id") == expected
