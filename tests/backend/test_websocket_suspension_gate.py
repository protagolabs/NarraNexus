"""
@file_name: test_websocket_suspension_gate.py
@author: Bin Liang
@date: 2026-08-13
@description: The WebSocket account-state gate on /ws/agent/run.

WS is the product's MAIN run path, and the HTTP auth middleware exempts /ws/*
(WebSocket carries its own auth in the first message). So the suspension gate
has to be enforced inside the WS handler too: a suspended user's valid JWT must
NOT be able to start a fresh agent run. These tests drive the handshake with a
TestClient and assert:

- a suspended (banned/blocked/deleted) user is refused with an
  ``account_suspended`` error frame and the socket is closed BEFORE any run
  work begins;
- an active user passes the gate (proven by reaching the circuit-breaker check
  just past it — mocked to skip so no real run starts).

The gate reuses the same shared NON_TRANSACTING_USER_STATUSES set and the same
_account_state reader as the HTTP middleware, so the two cannot drift.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend.auth import create_token
from backend.auth_errors import ACCOUNT_SUSPENDED
import backend.routes.websocket as ws_mod


@pytest.fixture
def force_cloud_mode(monkeypatch):
    # The handler calls the _is_cloud_mode it imported into its own namespace.
    monkeypatch.setattr(ws_mod, "_is_cloud_mode", lambda: True)


@pytest.fixture
def clear_state_cache():
    auth_mod._account_state_cache.clear()
    yield
    auth_mod._account_state_cache.clear()


@pytest.fixture
def wire_db(monkeypatch, db_client):
    """Point _account_state's lazy get_db_client at the in-memory test DB."""
    import xyz_agent_context.utils.db.db_factory as db_factory

    async def _ret():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", lambda: _ret())
    return db_client


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_mod.router)
    return app


async def _seed(db_client, user_id, status="active"):
    await db_client.insert(
        "users", {"user_id": user_id, "user_type": "individual", "status": status}
    )


def _first_message(user_id):
    return {
        "agent_id": "agent_1",
        "user_id": user_id,
        "input_content": "hello",
        "token": create_token(user_id, "user"),
    }


@pytest.mark.parametrize("status", ["banned", "blocked", "deleted"])
@pytest.mark.asyncio
async def test_suspended_user_ws_run_refused(
    force_cloud_mode, clear_state_cache, wire_db, status
):
    await _seed(wire_db, "bob", status=status)
    client = TestClient(_build_app())

    with client.websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_first_message("bob"))
        frame = ws.receive_json()

    assert frame["type"] == "error"
    assert frame["error_code"] == ACCOUNT_SUSPENDED


@pytest.mark.asyncio
async def test_active_user_ws_passes_the_gate(
    force_cloud_mode, clear_state_cache, wire_db, monkeypatch
):
    """An active user gets PAST the suspension gate. Proven by reaching the
    circuit-breaker check immediately after it (mocked to skip), so no real
    agent run is started and the first frame is the circuit frame, NOT
    account_suspended."""
    await _seed(wire_db, "alice", status="active")

    async def _skip(_agent_id):
        return True, "cooling"

    import xyz_agent_context.agent_framework.loop.circuit_breaker as cb_mod

    monkeypatch.setattr(cb_mod, "should_skip", _skip)

    client = TestClient(_build_app())

    with client.websocket_connect("/ws/agent/run") as ws:
        ws.send_json(_first_message("alice"))
        frame = ws.receive_json()

    # Not blocked by suspension...
    assert frame.get("error_code") != ACCOUNT_SUSPENDED
    # ...and it reached the circuit-breaker gate just past the account check.
    assert frame["error_type"] == "agent_circuit_open"
