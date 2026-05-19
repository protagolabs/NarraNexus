"""
@file_name: test_ws_exception_filter.py
@author: Bin Liang
@date: 2026-05-19
@description: The per-thread asyncio loop running the Lark SDK WebSocket
client must filter known-transient connection exceptions from its
default-exception-handler output. The SDK internally fires off
`loop.create_task(...)` for incoming-message handling and reconnection
plumbing; when the upstream WS resets, those tasks throw
`ConnectionResetError` / `websockets.ConnectionClosedError` with no
awaiter, and the asyncio default handler dumps "Task exception was never
retrieved" + a full traceback per occurrence.

The outer `while t.is_alive() and self.running` loop in `_subscribe_loop`
already handles disconnect by raising its own exception (so the backoff
+ reconnect path runs). The dropped tasks are pure log noise.

Observed in EC2 lark container 2026-05-18T19:16:35 / 19:24:17:
  ConnectionResetError: [Errno 104] Connection reset by peer
  websockets.exceptions.ConnectionClosedError: keepalive ping timeout
  asyncio Task exception was never retrieved
"""
from __future__ import annotations

from xyz_agent_context.module.lark_module.lark_trigger import (
    _ws_loop_exception_filter,
)


class _FakeLoop:
    """Stand-in for an asyncio loop that just records default-handler calls."""

    def __init__(self):
        self.default_handler_called_with: list[dict] = []

    def default_exception_handler(self, context):
        self.default_handler_called_with.append(context)


def test_filter_swallows_connection_reset_error():
    loop = _FakeLoop()
    ctx = {"exception": ConnectionResetError(104, "Connection reset by peer")}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.default_handler_called_with == []


def test_filter_swallows_os_error():
    loop = _FakeLoop()
    ctx = {"exception": OSError("broken pipe")}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.default_handler_called_with == []


def test_filter_swallows_websockets_module_exceptions():
    """Hand-built type from the `websockets.*` namespace; we filter by
    module prefix so we don't take a hard dependency on the SDK's
    specific exception classes."""
    loop = _FakeLoop()

    class _FakeWsExc(Exception):
        pass

    _FakeWsExc.__module__ = "websockets.exceptions"
    ctx = {"exception": _FakeWsExc("keepalive ping timeout")}

    _ws_loop_exception_filter(loop, ctx)
    assert loop.default_handler_called_with == []


def test_filter_passes_unknown_exception_through():
    loop = _FakeLoop()
    ctx = {"exception": ValueError("unrelated bug")}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.default_handler_called_with == [ctx]


def test_filter_passes_context_without_exception_through():
    """Loops occasionally call handler with no exception (e.g. slow callback
    warnings). Don't swallow those."""
    loop = _FakeLoop()
    ctx = {"message": "Slow callback detected"}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.default_handler_called_with == [ctx]
