"""
@file_name: test_executor_busy_flag.py
@date: 2026-08-20
@description: The executor reports whether work is in flight, so the broker's
idle reaper can refuse to stop a container that is working.

The broker only ever sees turn START (ensure()); the backend then streams
against the container directly. Without this flag a turn that outlives the
idle TTL gets culled out from under itself — the 2026-07-31 failure mode,
third incarnation. Binding rule #14 makes long turns first-class, so the
answer cannot be "keep the TTL big enough".

Driven through the ASGI layer, because that is where the accounting lives and
the reason it lives there is a real leak: a handler-level counter never comes
back down when the consumer disconnects before the first byte.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import xyz_agent_context.agent_runtime.executor_service as svc

_TURN = {"framework": "claude_code", "working_path": "/tmp/ws", "messages": []}


def _scope(path: str, method: str = "POST") -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": b"", "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234), "server": ("executor", 8020),
    }


async def _run_asgi(path: str, *, body: dict | None = None, stop_after: int | None = None):
    """Drive the app as ASGI and return the frames sent. ``stop_after``
    simulates the consumer vanishing mid-stream: the send callable raises,
    exactly as uvicorn's does once the socket is gone."""
    sent: list[dict] = []
    payload = json.dumps(body or {}).encode()
    delivered = False

    async def receive():
        # Body once, then disconnect. StreamingResponse runs a
        # listen_for_disconnect task that polls receive() until it sees
        # http.disconnect — replaying http.request forever spins it.
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if stop_after is not None and len(sent) > stop_after:
            raise OSError("client disconnected")

    try:
        await svc.app(_scope(path), receive, send)
    except OSError:
        pass
    return sent


@pytest.fixture(autouse=True)
def _reset_counter():
    svc._inflight_started.clear()
    yield
    svc._inflight_started.clear()


def test_idle_executor_reports_not_busy():
    assert svc.health() == {
        "status": "healthy", "busy": False, "inflight_work": 0,
        "inflight_oldest_s": None,
    }


@pytest.mark.asyncio
async def test_busy_for_the_whole_turn_and_clear_afterwards(monkeypatch):
    """Busy must hold at every point BETWEEN frames — a tool call can run for
    minutes with no output, which is exactly when a container looks idle."""
    mid_turn: list[dict] = []

    class _SlowDriver:
        async def agent_loop(self, *a, **kw):
            yield {"type": "progress", "step": "0"}
            mid_turn.append(svc.health())
            yield {"type": "agent_response", "delta": "done"}

    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **kw: _SlowDriver())

    await _run_asgi("/agent-loop", body=_TURN)

    assert len(mid_turn) == 1
    assert mid_turn[0]["busy"] is True
    assert mid_turn[0]["inflight_work"] == 1
    # Age, not just a count: a bare count cannot tell a legitimate 10-hour
    # turn from a request pinned open forever, and the broker now refuses to
    # reap either. This makes "something has been in flight for 8 hours" an
    # observable fact rather than an assumption.
    assert mid_turn[0]["inflight_oldest_s"] >= 0
    assert svc.health()["busy"] is False
    assert svc.health()["inflight_oldest_s"] is None


@pytest.mark.asyncio
async def test_counter_clears_when_the_consumer_disconnects_before_the_first_byte(
    monkeypatch,
):
    """THE regression this middleware exists for.

    StreamingResponse sends http.response.start BEFORE it touches the body
    iterator, so a consumer that is already gone means the generator is never
    started — and closing a never-started async generator runs none of its
    code, including a finally. A handler-level counter leaks here PERMANENTLY:
    the container reports busy forever, the broker's reaper refuses it forever,
    and the slot never comes back.
    """
    class _Driver:
        async def agent_loop(self, *a, **kw):
            yield {"type": "progress", "step": "0"}

    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **kw: _Driver())

    await _run_asgi("/agent-loop", body=_TURN, stop_after=0)   # dies on start

    assert svc._inflight_started == {}
    assert svc.health()["busy"] is False


@pytest.mark.asyncio
async def test_counter_clears_when_the_consumer_disconnects_mid_stream(monkeypatch):
    class _Endless:
        async def agent_loop(self, *a, **kw):
            while True:
                yield {"type": "agent_response", "delta": "."}

    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **kw: _Endless())

    await _run_asgi("/agent-loop", body=_TURN, stop_after=2)

    assert svc._inflight_started == {}


@pytest.mark.asyncio
async def test_counter_clears_when_the_turn_raises(monkeypatch):
    """A crashed turn frees the container; leaving it marked busy forever
    would make it un-reapable — the leak this flag must not introduce."""
    class _BoomDriver:
        async def agent_loop(self, *a, **kw):
            yield {"type": "progress", "step": "0"}
            raise RuntimeError("driver exploded")

    monkeypatch.setattr(svc, "get_agent_loop_driver", lambda *a, **kw: _BoomDriver())

    sent = await _run_asgi("/agent-loop", body=_TURN)

    assert "driver exploded" in b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    ).decode()
    assert svc._inflight_started == {}


@pytest.mark.asyncio
async def test_the_health_probe_itself_never_marks_the_container_busy():
    """Otherwise the broker's probe would be self-fulfilling: every read would
    report the container as working."""
    seen: list[dict] = []

    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            seen.append(json.loads(message["body"]))

    await svc.app(_scope("/health", method="GET"), receive, send)

    assert seen and seen[0]["busy"] is False


def test_work_path_contract():
    """The office-watch endpoints proxy to a server running INSIDE this
    container, so reaping under them kills that session too — "busy" means
    the container is in use, not merely that an agent turn is streaming.
    /health must stay outside the set or the broker's probe would be
    self-fulfilling."""
    assert "/agent-loop".startswith(svc._WORK_PATH_PREFIXES)
    assert "/watch/ensure".startswith(svc._WORK_PATH_PREFIXES)
    assert "/watch/8080/index.html".startswith(svc._WORK_PATH_PREFIXES)
    assert not "/health".startswith(svc._WORK_PATH_PREFIXES)


def test_concurrent_work_is_counted():
    """One user can drive several agents at once; the container stays busy
    until the LAST of them finishes, and the reported age is the OLDEST."""
    from time import monotonic
    svc._inflight_started.update({1: monotonic() - 30.0, 2: monotonic()})
    body = svc.health()
    assert (body["busy"], body["inflight_work"]) == (True, 2)
    assert body["inflight_oldest_s"] >= 30.0
    svc._inflight_started.clear()
    assert svc.health()["busy"] is False


@pytest.mark.asyncio
async def test_a_body_that_never_arrives_cannot_pin_the_container(monkeypatch):
    """/agent-loop is unauthenticated and reachable from INSIDE this
    container — the agent's own Bash can POST a chunked body and never close
    it. Without a parse budget request.json() waits forever while the
    container stays marked busy, i.e. never reapable: the untrusted sandbox
    would be deciding whether the broker may reclaim its own slot.

    Bounding the BODY read is not a ceiling on the turn (rule #14): once the
    body is in, the loop runs as long as it likes.
    """
    monkeypatch.setattr(svc, "_BODY_READ_TIMEOUT_S", 0.05)

    class _NeverEndingBody:
        async def json(self):
            await asyncio.sleep(3600)

    resp = await svc.agent_loop(_NeverEndingBody())

    assert resp.status_code == 408
    assert svc._inflight_started == {}
