"""
@file_name: test_activity_log_a2a_visibility.py
@author:
@date: 2026-08-21
@description: The Activity Log (simple-chat-history) must surface the agent's
A2A / team activity to its OWNER, and must NOT leak it to anyone else.

A2A and team turns run through MessageBusTrigger, which invokes the runtime with
``user_id = sender_agent_id`` (the peer/team id, not the owner). Their turns are
therefore stored in ChatModule instances keyed to that peer id, so the
owner-scoped query (``get_by_agent_and_user(agent_id, owner)``) never sees them
and the Activity Log showed none of the agent's peer/team activity.

The fix pulls the agent's peer-scoped ChatModule instances too, but ONLY for the
owner, and only their background (a2a / message_bus) activity rows — never a
user-facing chat row, and never for a non-owner sharing a public agent.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.agents.chat_history as ch

OWNER_ID = "owner_u1"
AGENT_ID = "agent_mine"
PEER_ID = "agent_peer"
PEER_REPLY = "SECRET peer-to-peer content"


def _memory(*msgs):
    return json.dumps({"messages": list(msgs)})


@pytest.fixture
async def client(monkeypatch):
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend_db = SQLiteBackend(":memory:")
    await backend_db.initialize()
    await auto_migrate(backend_db)
    db = await AsyncDatabaseClient.create_with_backend(backend_db)

    await db.insert("agents", {
        "agent_id": AGENT_ID, "agent_name": "Daolai",
        "created_by": OWNER_ID, "agent_type": "chat",
    })

    # Owner's own chat with the agent.
    await db.insert("module_instances", {
        "instance_id": "chat_owner", "agent_id": AGENT_ID, "user_id": OWNER_ID,
        "module_class": "ChatModule", "status": "active",
    })
    await db.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_owner",
        "memory": _memory({"role": "user", "content": "hi mine"},
                          {"role": "assistant", "content": "hello owner"}),
    })

    # A peer-agent (A2A) turn — stored under the PEER's id as user_id.
    await db.insert("module_instances", {
        "instance_id": "chat_peer", "agent_id": AGENT_ID, "user_id": PEER_ID,
        "module_class": "ChatModule", "status": "active",
    })
    await db.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_peer",
        "memory": _memory(
            {"role": "user", "content": "peer prompt"},
            {"role": "assistant", "content": PEER_REPLY,
             "meta_data": {"working_source": "message_bus"}},
        ),
    })

    # An unrelated third user's private chat with the same agent.
    await db.insert("module_instances", {
        "instance_id": "chat_intruder", "agent_id": AGENT_ID, "user_id": "intruder_u2",
        "module_class": "ChatModule", "status": "active",
    })
    await db.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_intruder",
        "memory": _memory({"role": "user", "content": "intruder hi"},
                          {"role": "assistant", "content": "hello intruder"}),
    })

    async def _db():
        return db

    monkeypatch.setattr(ch, "get_db_client", _db)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(ch.router, prefix="/api/agents")
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        await db.close()


def _history(client, user):
    r = client.get(f"/api/agents/{AGENT_ID}/simple-chat-history",
                   params={"limit": 50}, headers={"x-test-user": user})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    return body["messages"], r.text


def test_owner_sees_peer_activity_as_collapsed_row(client):
    msgs, raw = _history(client, OWNER_ID)
    bus = [m for m in msgs if m.get("working_source") == "message_bus"]
    # Non-vacuous: without the fix the peer instance is filtered out and this
    # list is empty.
    assert bus, "owner should see the A2A/team activity row"
    assert all(m.get("message_type") == "activity" for m in bus)
    # The peer's raw reply text is collapsed, never surfaced verbatim.
    assert PEER_REPLY not in raw
    # The owner's own chat is still there.
    assert any(m.get("content") == "hi mine" for m in msgs)


def test_non_owner_never_sees_peer_activity(client):
    msgs, raw = _history(client, "intruder_u2")
    assert not any(m.get("working_source") == "message_bus" for m in msgs), \
        "a non-owner must not see the agent's peer/team activity"
    assert PEER_REPLY not in raw
    # The intruder still sees their OWN chat with the agent.
    assert any(m.get("content") == "intruder hi" for m in msgs)
