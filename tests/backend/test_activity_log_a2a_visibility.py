"""
@file_name: test_activity_log_a2a_visibility.py
@author:
@date: 2026-08-21
@description: The Activity Log (simple-chat-history, include=activity) must
surface the agent's A2A / team activity to its OWNER, must NOT leak it to anyone
else or leak any other user's chat, and must NOT starve the conversation stream.

A2A and team turns run through MessageBusTrigger, which invokes the runtime with
``user_id = sender_agent_id`` (the peer agent id for A2A, ``team_<id>`` for a
team room — not the owner). Their turns are therefore stored in ChatModule
instances keyed to that peer/team id, which the owner-scoped query never
returns, so the Activity Log showed none of the agent's peer/team activity.

The fix pulls the agent's peer-scoped ChatModule instances too, but ONLY for the
owner, ONLY instances whose user_id is a peer/team scope (prefix), and surfaces
ONLY their background a2a / message_bus rows (collapsed to a compact activity
marker). The conversation and activity streams paginate independently via the
``include`` param so a flood of peer activity cannot empty the conversation tab.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.agents.chat_history as ch

OWNER_ID = "owner_u1"
AGENT_ID = "agent_mine"
PEER_REPLY = "SECRET peer-to-peer content"
TEAM_REPLY = "SECRET team-room content"


def _msg(role, content, ts, source=None):
    meta = {"timestamp": ts}
    if source:
        meta["working_source"] = source
    return {"role": role, "content": content, "meta_data": meta}


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

    async def _instance(iid, uid, *msgs):
        await db.insert("module_instances", {
            "instance_id": iid, "agent_id": AGENT_ID, "user_id": uid,
            "module_class": "ChatModule", "status": "active",
        })
        await db.insert("instance_json_format_memory_chat",
                        {"instance_id": iid, "memory": _memory(*msgs)})

    # Owner's own instance: one conversation turn (early) PLUS a flood of the
    # owner's own background activity (job-source assistant rows, collapsed to
    # message_type == "activity"), all LATER than the conversation. These are
    # owner-scoped so they land in every include and let the conversation-stream
    # starvation test bite without needing the peer pull.
    owner_msgs = [
        _msg("user", "hi mine", "2026-08-21T06:00:00"),
        _msg("assistant", "hello owner", "2026-08-21T06:00:01"),
    ]
    owner_msgs += [
        _msg("assistant", f"ran job #{i}", f"2026-08-21T09:{i:02d}:00", "job")
        for i in range(30)
    ]
    await _instance("chat_owner", OWNER_ID, *owner_msgs)

    # A2A peer instance: a user-facing prompt row + assistant replies, keyed to
    # the peer AGENT id. The replies are stored as message_bus turns (LATER than
    # the owner chat, to exercise starvation).
    peer_msgs = [_msg("user", "peer prompt", "2026-08-21T07:00:00")]
    peer_msgs += [
        _msg("assistant", f"{PEER_REPLY} #{i}", f"2026-08-21T07:{i:02d}:00", "message_bus")
        for i in range(30)
    ]
    await _instance("chat_peer", "agent_peer", *peer_msgs)

    # Team-room instance (team_ prefix), assistant reply stored as an a2a turn —
    # covers the OTHER _A2A_TEAM_SOURCES value and the team prefix.
    await _instance("chat_team", "team_room1",
                    _msg("assistant", TEAM_REPLY, "2026-08-21T08:00:00", "a2a"))

    # An unrelated third HUMAN user's private chat with the same agent. Its
    # user_id is not a peer/team prefix, so it must never be pulled at all.
    await _instance("chat_intruder", "intruder_u2",
                    _msg("user", "intruder hi", "2026-08-21T06:30:00"),
                    _msg("assistant", "hello intruder", "2026-08-21T06:30:01"))

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


def _history(client, user, include="all", limit=50):
    r = client.get(f"/api/agents/{AGENT_ID}/simple-chat-history",
                   params={"limit": limit, "include": include},
                   headers={"x-test-user": user})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    return body["messages"], r.text


def test_owner_activity_stream_shows_peer_and_team(client):
    msgs, raw = _history(client, OWNER_ID, include="activity", limit=500)
    sources = {m.get("working_source") for m in msgs}
    # Non-vacuous: without the peer pull neither appears. Both bus sources land.
    assert "message_bus" in sources, "A2A peer activity missing"
    assert "a2a" in sources, "team-room (a2a) activity missing"
    assert all(m.get("message_type") == "activity" for m in msgs)
    # Raw peer/team reply text is collapsed, never surfaced verbatim.
    assert PEER_REPLY not in raw and TEAM_REPLY not in raw


def test_owner_isolation_drops_peer_userfacing_and_other_users(client):
    # include=all returns conversation rows too, so a leaked peer/other-user
    # row WOULD show up here. Pins two nets: the peer-instance user_id prefix
    # filter (intruder never pulled) and the row-level working_source guard
    # (peer instance's own user-facing "peer prompt" dropped).
    # High limit so the assertions cover EVERY row (leak checks, not paging).
    msgs, raw = _history(client, OWNER_ID, include="all", limit=500)
    assert "peer prompt" not in raw          # peer instance user-facing row
    assert "intruder hi" not in raw          # other human's chat (prefix-filtered)
    assert "hello intruder" not in raw
    assert PEER_REPLY not in raw and TEAM_REPLY not in raw
    assert any(m.get("content") == "hi mine" for m in msgs)  # owner's own chat


def test_non_owner_never_sees_peer_activity(client):
    msgs, raw = _history(client, "intruder_u2", include="all", limit=500)
    assert not any(m.get("working_source") in ("a2a", "message_bus") for m in msgs)
    assert PEER_REPLY not in raw and TEAM_REPLY not in raw
    assert any(m.get("content") == "intruder hi" for m in msgs)  # own chat intact


def test_chat_stream_not_starved_by_activity_flood(client):
    # 30 owner-scoped activity rows (job source) are all NEWER than the owner's
    # one conversation turn. A shared 20-row budget would return all-activity
    # and empty the conversation tab; include='chat' must slice its OWN stream
    # so the conversation turn survives. Non-vacuous: drop the include split and
    # the last-20 becomes the job flood, and "hi mine" disappears.
    msgs, _ = _history(client, OWNER_ID, include="chat", limit=20)
    assert any(m.get("content") == "hi mine" for m in msgs), \
        "conversation stream starved by the activity flood"
    assert all(m.get("message_type") != "activity" for m in msgs)
