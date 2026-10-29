"""
@file_name: test_executor_api_token.py
@date: 2026-10-29
@description: The per-executor API token, end to end on the orchestrator and
executor sides (sandbox isolation phase 3).

The executor's API (:8020) used to be unauthenticated ("internal-trust"), so
tenant isolation rested on the host firewall alone — which silently did
nothing on prod for a day (2026-10-29). The broker now plants a random token in
each executor's env and returns it on ensure(); every orchestrator call must
present it and the executor rejects calls without it.

Rollout-safety is part of the contract and pinned here too: the executor only
enforces when its env carries a token, and callers only send one when the
broker gave them one — so either repo can deploy first without a 401.
"""
from __future__ import annotations

import importlib

import aiohttp
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from narranexus.platform.agent_framework.loop import broker_client
from narranexus.platform.agent_framework.loop.broker_client import ExecutorEnsureResult
from narranexus.platform.agent_framework.loop.remote_driver import RemoteAgentLoopDriver
from narranexus.platform.agent_runtime import executor_protocol as proto
from narranexus.platform.agent_runtime import executor_service as es


# ------------------------------------------------------------ wire helper


def test_auth_headers_present_only_with_a_token():
    assert proto.executor_auth_headers("tok") == {"Authorization": "Bearer tok"}
    assert proto.executor_auth_headers(None) == {}
    assert proto.executor_auth_headers("") == {}


# ------------------------------------------------------------ executor side


def _app(token):
    async def ok(request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[
        Route("/health", ok),
        Route("/agent-loop", ok, methods=["POST"]),
        Route("/watch/{rest:path}", ok),
    ])
    return TestClient(es.ExecutorTokenMiddleware(inner, token=token))


def test_executor_rejects_calls_without_the_token():
    c = _app("s3cret")
    assert c.post("/agent-loop").status_code == 401
    assert c.post("/agent-loop", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/watch/1/x", headers={"Authorization": "s3cret"}).status_code == 401


def test_executor_accepts_the_right_token():
    c = _app("s3cret")
    assert c.post("/agent-loop", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/watch/1/x", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_health_stays_open_for_the_broker_and_readiness_probes():
    assert _app("s3cret").get("/health").status_code == 200


def test_no_token_in_env_means_no_enforcement():
    # An executor spawned by a broker that predates tokens has none; it must
    # keep working for the orchestrator that sends nothing (rollout safety).
    c = _app(None)
    assert c.post("/agent-loop").status_code == 200


def test_the_token_middleware_wraps_outermost():
    # Outermost, so a rejected call never reaches InFlightWorkMiddleware and
    # never marks the container busy (= unreapable). Starlette applies
    # user_middleware[0] outermost.
    assert es.app.user_middleware[0].cls is es.ExecutorTokenMiddleware


@pytest.mark.asyncio
async def test_websocket_without_token_is_refused():
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    sent = []

    async def send(msg):
        sent.append(msg)

    mw = es.ExecutorTokenMiddleware(inner, token="s3cret")
    await mw({"type": "websocket", "path": "/ws", "headers": []}, None, send)
    assert reached == []
    assert sent == [{"type": "websocket.close", "code": 1008}]
    # lifespan is the only scope that bypasses the check
    await mw({"type": "lifespan"}, None, send)
    assert reached == ["lifespan"]


def test_token_is_taken_out_of_the_environment(monkeypatch):
    # Agent subprocesses inherit os.environ; the token must not ride along.
    monkeypatch.setenv(proto.EXECUTOR_TOKEN_ENV, "planted")
    assert es._take_executor_token() == "planted"
    import os

    assert proto.EXECUTOR_TOKEN_ENV not in os.environ


def test_token_is_read_from_the_env_the_broker_plants(monkeypatch):
    monkeypatch.setenv(proto.EXECUTOR_TOKEN_ENV, "from-env")
    assert es._executor_token_from_env() == "from-env"
    monkeypatch.delenv(proto.EXECUTOR_TOKEN_ENV)
    assert es._executor_token_from_env() is None


# ------------------------------------------------------------ broker client


@pytest.mark.asyncio
async def test_ensure_result_carries_the_executor_token(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")

    def handler(request):
        return httpx.Response(200, json={
            "status": "reused",
            "executor_url": "http://nx-exec-a:8020",
            "executor_token": "tok-a",
        })

    real = httpx.AsyncClient
    monkeypatch.setattr(
        broker_client.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    out = await broker_client.ensure_executor("a")
    assert out.executor_token == "tok-a"


@pytest.mark.asyncio
async def test_old_broker_without_token_yields_none(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")

    def handler(request):
        return httpx.Response(200, json={"status": "reused", "executor_url": "http://x:8020"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        broker_client.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    assert (await broker_client.ensure_executor("a")).executor_token is None


# ------------------------------------------------------------ remote driver


class _Resp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    @property
    def content(self):
        return self

    async def iter_any(self):
        yield b'{"event": {"type": "done"}}\n'


class _Session:
    def __init__(self):
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json, timeout=None):
        self.posts.append(url)
        return _Resp()


def _capture_session(monkeypatch):
    seen = {}
    session = _Session()

    def factory(*a, **kw):
        seen.update(kw)
        return session

    monkeypatch.setattr(aiohttp, "ClientSession", factory)
    return seen


@pytest.mark.asyncio
async def test_remote_driver_presents_the_token_on_its_session(monkeypatch):
    # Session-level, so /agent-loop AND every /steer POST on it carry it.
    seen = _capture_session(monkeypatch)
    d = RemoteAgentLoopDriver(
        framework="claude_code", working_path="/tmp",
        executor_url="http://exec:8020", executor_token="tok",
    )
    _ = [e async for e in d.agent_loop(messages=[], mcp_servers={})]
    assert seen["headers"] == {"Authorization": "Bearer tok"}


@pytest.mark.asyncio
async def test_remote_driver_sends_no_auth_without_a_token(monkeypatch):
    seen = _capture_session(monkeypatch)
    d = RemoteAgentLoopDriver(framework="claude_code", working_path="/tmp", executor_url="http://exec:8020")
    _ = [e async for e in d.agent_loop(messages=[], mcp_servers={})]
    assert not seen.get("headers")


def test_factory_hands_the_token_to_the_remote_driver_only(monkeypatch):
    from narranexus.platform.agent_framework.loop.driver import get_agent_loop_driver

    d = get_agent_loop_driver(
        "claude_code", executor_url="http://exec:8020", executor_token="tok", working_path="/tmp",
    )
    assert isinstance(d, RemoteAgentLoopDriver)
    assert d._auth_headers == {"Authorization": "Bearer tok"}


@pytest.mark.asyncio
async def test_a_rejected_token_is_a_typed_platform_failure(monkeypatch):
    # A 401 must not fall through as a generic ClientResponseError, which the
    # helper-LLM fallback would paper over with a fabricated reply every turn.
    from narranexus.platform.agent_framework.llm.failure import (
        EXECUTOR_INFRA_REASON_UNREACHABLE,
        classify_executor_infra_failure,
    )
    from narranexus.platform.agent_framework.loop.executor_errors import (
        ExecutorAuthError,
        ExecutorUnreachableError,
    )

    class _Denied(_Resp):
        status = 401

        def raise_for_status(self):
            raise AssertionError("401 must be typed before raise_for_status")

    class _DeniedSession(_Session):
        def post(self, url, json, timeout=None):
            return _Denied()

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _DeniedSession())
    d = RemoteAgentLoopDriver(framework="claude_code", working_path="/tmp",
                              executor_url="http://exec:8020", executor_token="stale")
    with pytest.raises(ExecutorAuthError) as ei:
        _ = [e async for e in d.agent_loop(messages=[], mcp_servers={})]
    assert isinstance(ei.value, ExecutorUnreachableError)
    assert classify_executor_infra_failure("ExecutorAuthError", "") == EXECUTOR_INFRA_REASON_UNREACHABLE


# ------------------------------------------------------------ step 3 wiring


def test_step3_takes_url_and_token_from_the_ensure_result():
    step3 = importlib.import_module(
        "narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )
    ensured = ExecutorEnsureResult(url="http://nx-exec-a:8020", cold_started=False, executor_token="tok")
    assert step3._executor_target(ensured) == ("http://nx-exec-a:8020", "tok")
    assert step3._executor_target(None) == (None, None)


def test_step3_builds_the_turn_driver_with_the_token():
    step3 = importlib.import_module(
        "narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )
    ensured = ExecutorEnsureResult(url="http://nx-exec-a:8020", cold_started=False, executor_token="tok")
    d = step3._build_turn_driver("claude_code", ensured, "/tmp")
    assert isinstance(d, RemoteAgentLoopDriver)
    assert d._auth_headers == {"Authorization": "Bearer tok"}


def test_step3_body_builds_its_driver_through_the_tested_seam():
    # The seam above is only worth testing if the turn actually uses it.
    import inspect

    step3 = importlib.import_module(
        "narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )
    body = inspect.getsource(step3.step_3_agent_loop)
    assert "_build_turn_driver(" in body
    assert "get_agent_loop_driver(" not in body


# ------------------------------------------------------------ office watch proxy


@pytest.mark.asyncio
async def test_office_watch_calls_present_the_token(monkeypatch):
    owp = importlib.import_module("backend.routes.office_watch.proxy")

    async def fake_ensure(user_id, **kw):
        return ExecutorEnsureResult(url="http://nx-exec-a:8020", cold_started=False, executor_token="tok")

    async def ready(url, **kw):
        return None

    monkeypatch.setattr(owp, "ensure_executor", fake_ensure)
    monkeypatch.setattr(owp, "wait_until_ready", ready)

    url, headers = await owp._resolve_upstream("a", 7900, "index.html", "")
    assert url == "http://nx-exec-a:8020/watch/7900/index.html"
    assert headers == {"Authorization": "Bearer tok"}

    sessions = []

    class _R:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            return {"port": 7900, "mtime": 1, "size": 2}

    class _S:
        def __init__(self, **kw):
            sessions.append(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return _R()

        def get(self, *a, **k):
            return _R()

    monkeypatch.setattr(owp.aiohttp, "ClientSession", _S)
    assert await owp._ensure_watch_in_executor("a", "agent_x", "doc.docx") == 7900
    await owp._file_version_in_executor("a", "agent_x", "doc.docx")
    assert all(s.get("headers") == {"Authorization": "Bearer tok"} for s in sessions)
    assert len(sessions) == 2


class _RecordingSession:
    """Records the headers of the one upstream call, then fails it so the
    proxy's own error path ends the request (no stream plumbing needed)."""

    def __init__(self, sink, **kw):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def close(self):
        return None

    async def get(self, url, headers=None, **k):
        self._sink.append(headers or {})
        raise aiohttp.ClientConnectionError("stop here")

    async def post(self, url, data=None, headers=None, **k):
        self._sink.append(headers or {})
        raise aiohttp.ClientConnectionError("stop here")


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [True, False])
async def test_office_watch_passthrough_presents_the_token(monkeypatch, cloud):
    from fastapi import HTTPException

    owp = importlib.import_module("backend.routes.office_watch.proxy")

    async def fake_ensure(user_id, **kw):
        if not cloud:
            return None
        return ExecutorEnsureResult(url="http://nx-exec-a:8020", cold_started=False, executor_token="tok")

    monkeypatch.setattr(owp, "ensure_executor", fake_ensure)
    sink = []
    monkeypatch.setattr(owp.aiohttp, "ClientSession", lambda **kw: _RecordingSession(sink, **kw))

    with pytest.raises(HTTPException):
        await owp._proxy_stream("a", 7900, "index.html", "", "/p", {"accept": "text/html"})
    with pytest.raises(HTTPException):
        await owp._post_upstream("a", 7900, "api/save", "", b"{}", "application/json")

    assert len(sink) == 2
    for headers in sink:
        if cloud:
            assert headers.get("Authorization") == "Bearer tok"
        else:
            assert "Authorization" not in headers
