"""
@file_name: test_chat_latest.py
@author: Bin Liang
@date: 2026-10-30
@description: GET /api/agents/chat-latest — the sidebar's background-reply signal — reports exactly the newest row of the conversation tab, for a whole agent list in one call.

The sidebar used to call simple-chat-history(include='chat') once per agent
every 15s and compare the newest message's timestamp. /chat-latest replaces the
fan-out with one call; its contract is PARITY with that tab: whatever the tab's
newest row is (owner-notify carve-outs included, collapsed IM/job activity
excluded), and nothing else. The parity test below computes the expected value
through the real simple-chat-history endpoint, so the two cannot drift.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import narranexus_plugins.chat_module._chat_latest as cl
import narranexus_plugins.chat_module.routes as ch

OWNER = "owner_u1"
OTHER = "other_u2"


def _msg(role, content, ts, source=None, notify=None):
    meta = {"timestamp": ts}
    if source:
        meta["working_source"] = source
    if notify is not None:
        meta["owner_notify_content"] = notify
    return {"role": role, "content": content, "meta_data": meta}


def _memory(*msgs):
    return json.dumps({"messages": list(msgs)})


@pytest.fixture
async def env(monkeypatch):
    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    from narranexus.platform.utils.db.schema_registry import auto_migrate
    from backend.plugin_sdk_host import install_web_host

    backend_db = SQLiteBackend(":memory:")
    await backend_db.initialize()
    await auto_migrate(backend_db)
    db = await AsyncDatabaseClient.create_with_backend(backend_db)

    async def instance(iid, agent_id, uid, *msgs, status="active", updated_at="2026-10-30 00:00:00.000001"):
        await db.insert("module_instances", {
            "instance_id": iid, "agent_id": agent_id, "user_id": uid,
            "module_class": "ChatModule", "status": status,
        })
        await db.insert("instance_json_format_memory_chat",
                        {"instance_id": iid, "memory": _memory(*msgs), "updated_at": updated_at})

    async def _db():
        return db

    monkeypatch.setattr(ch, "get_db_client", _db)
    cl._LATEST_CACHE.clear()
    install_web_host()

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(ch.router, prefix="/api/agents")
    client = TestClient(app)
    try:
        yield client, db, instance
    finally:
        cl._LATEST_CACHE.clear()
        await db.close()


def _latest(client, user, agent_ids):
    r = client.get("/api/agents/chat-latest", params={"agent_ids": ",".join(agent_ids)},
                   headers={"x-test-user": user})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    return body["latest"]


def _tab_newest(client, user, agent_id):
    r = client.get(f"/api/agents/{agent_id}/simple-chat-history",
                   params={"limit": 5, "include": "chat"}, headers={"x-test-user": user})
    msgs = r.json()["messages"]
    return msgs[-1]["timestamp"] if msgs else None


@pytest.mark.asyncio
async def test_parity_with_the_conversation_tab(env):
    client, _db, instance = env
    # a_chat: plain conversation; newest row is the agent's reply.
    await instance("c1", "a_chat", OWNER,
                   _msg("user", "hi", "2026-10-30T10:00:00"),
                   _msg("assistant", "hello", "2026-10-30T10:00:05"))
    # a_lark: an IM turn after the chat, NOT notifying the owner — collapsed to
    # activity, so the tab's newest row (and the signal) stays the chat reply.
    await instance("c2", "a_lark", OWNER,
                   _msg("user", "q", "2026-10-30T09:00:00"),
                   _msg("assistant", "a", "2026-10-30T09:00:01"),
                   _msg("assistant", "lark chatter", "2026-10-30T11:00:00", "lark"))
    # a_job: a job turn that DID notify the owner — shown in the tab, so it counts.
    await instance("c3", "a_job", OWNER,
                   _msg("user", "q", "2026-10-30T09:00:00"),
                   _msg("assistant", "job note", "2026-10-30T12:00:00", "job", notify="Report ready"))
    # a_user_last: the newest row is the owner's own message (the old poll counted it too).
    await instance("c4", "a_user_last", OWNER,
                   _msg("assistant", "earlier", "2026-10-30T08:00:00"),
                   _msg("user", "typed on another device", "2026-10-30T13:00:00"))
    # a_activity_only: nothing the tab shows -> no entry at all.
    await instance("c5", "a_activity_only", OWNER,
                   _msg("assistant", "job ran", "2026-10-30T14:00:00", "job"))

    agents = ["a_chat", "a_lark", "a_job", "a_user_last", "a_activity_only"]
    latest = _latest(client, OWNER, agents)
    for aid in agents:
        assert latest.get(aid) == _tab_newest(client, OWNER, aid), aid
    assert latest["a_lark"] == "2026-10-30T09:00:01"
    assert latest["a_job"] == "2026-10-30T12:00:00"
    assert "a_activity_only" not in latest


@pytest.mark.asyncio
async def test_only_the_callers_own_instances_count(env):
    client, _db, instance = env
    await instance("mine", "a1", OWNER, _msg("assistant", "to owner", "2026-10-30T10:00:00", "chat"))
    # Another user's private chat with the same (public) agent, later.
    await instance("theirs", "a1", OTHER, _msg("assistant", "to other", "2026-10-30T11:00:00", "chat"))
    # A peer (A2A) scope on the same agent, later still.
    await instance("peer", "a1", "agent_peer", _msg("assistant", "bus", "2026-10-30T12:00:00", "message_bus"))
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:00:00"}
    assert _latest(client, OTHER, ["a1"]) == {"a1": "2026-10-30T11:00:00"}


@pytest.mark.asyncio
async def test_cancelled_and_archived_instances_are_skipped(env):
    client, _db, instance = env
    await instance("live", "a1", OWNER, _msg("assistant", "x", "2026-10-30T10:00:00"))
    await instance("gone", "a1", OWNER, _msg("assistant", "y", "2026-10-30T11:00:00"), status="archived")
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:00:00"}


@pytest.mark.asyncio
async def test_a_new_message_is_seen_and_an_unchanged_row_is_not_reparsed(env, monkeypatch):
    client, db, instance = env
    await instance("c1", "a1", OWNER, _msg("assistant", "first", "2026-10-30T10:00:00"))
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:00:00"}

    parsed = []
    real = cl._parse_row
    monkeypatch.setattr(cl, "_parse_row", lambda *a: parsed.append(1) or real(*a))
    # Unchanged row: served from the cache, memory not read or parsed again.
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:00:00"}
    assert parsed == []

    # The writer upserts memory and bumps updated_at: the new reply is seen.
    await db.update("instance_json_format_memory_chat", {"instance_id": "c1"}, {
        "memory": _memory(_msg("assistant", "first", "2026-10-30T10:00:00"),
                          _msg("assistant", "second", "2026-10-30T10:05:00")),
        "updated_at": "2026-10-30 10:05:00.000002",
    })
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:05:00"}
    assert parsed == [1]


@pytest.mark.asyncio
async def test_a_whole_second_stamp_is_never_trusted(env):
    """SQLite's datetime('now') has whole seconds: a second write inside the
    same second keeps the same updated_at and must still be seen."""
    client, db, instance = env
    await instance("c1", "a1", OWNER, _msg("assistant", "first", "2026-10-30T10:00:00"),
                   updated_at="2026-10-30 10:00:00")
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:00:00"}
    await db.update("instance_json_format_memory_chat", {"instance_id": "c1"}, {
        "memory": _memory(_msg("assistant", "first", "2026-10-30T10:00:00"),
                          _msg("assistant", "second", "2026-10-30T10:00:00.5")),
        "updated_at": "2026-10-30 10:00:00",
    })
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T10:00:00.5"}


@pytest.mark.asyncio
async def test_several_instances_of_one_agent_report_the_newest(env):
    client, _db, instance = env
    await instance("old", "a1", OWNER, _msg("assistant", "x", "2026-10-30T10:00:00"))
    await instance("new", "a1", OWNER, _msg("assistant", "y", "2026-10-30T12:00:00"))
    assert _latest(client, OWNER, ["a1"]) == {"a1": "2026-10-30T12:00:00"}


@pytest.mark.asyncio
async def test_one_malformed_memory_row_costs_only_its_own_agent(env):
    """A list-shaped blob, a non-dict message, a null meta_data: each must cost
    only its own agent, not blank the signal for the whole list."""
    client, db, instance = env
    await instance("ok", "a_ok", OWNER, _msg("assistant", "fine", "2026-10-30T10:00:00"))
    await db.insert("module_instances", {"instance_id": "bad1", "agent_id": "a_bad1", "user_id": OWNER,
                                         "module_class": "ChatModule", "status": "active"})
    await db.insert("instance_json_format_memory_chat", {"instance_id": "bad1", "memory": json.dumps([1, 2])})
    await instance("bad2", "a_bad2", OWNER, {"role": "assistant", "content": "x", "meta_data": None})
    await db.insert("module_instances", {"instance_id": "bad3", "agent_id": "a_bad3", "user_id": OWNER,
                                         "module_class": "ChatModule", "status": "active"})
    await db.insert("instance_json_format_memory_chat",
                    {"instance_id": "bad3", "memory": json.dumps({"messages": ["not a dict"]})})
    assert _latest(client, OWNER, ["a_ok", "a_bad1", "a_bad2", "a_bad3"]) == {"a_ok": "2026-10-30T10:00:00"}


@pytest.mark.asyncio
async def test_ties_resolve_like_the_conversation_tab(env):
    """Equal parsed times, different raw strings: the tab's stable sort keeps the
    later message within an instance, and the later-listed (older) instance."""
    client, _db, instance = env
    await instance("c_new", "a1", OWNER,
                   _msg("assistant", "x", "2026-10-30T10:00:00Z"),
                   _msg("assistant", "y", "2026-10-30T10:00:00+00:00"))
    await instance("c_old", "a1", OWNER, _msg("assistant", "z", "2026-10-30T10:00:00.000"))
    assert _latest(client, OWNER, ["a1"])["a1"] == _tab_newest(client, OWNER, "a1")


@pytest.mark.asyncio
async def test_a_tie_inside_one_instance_keeps_the_later_message(env):
    client, _db, instance = env
    await instance("c1", "a1", OWNER,
                   _msg("assistant", "x", "2026-10-30T10:00:00Z"),
                   _msg("assistant", "y", "2026-10-30T10:00:00+00:00"))
    latest = _latest(client, OWNER, ["a1"])["a1"]
    assert latest == "2026-10-30T10:00:00+00:00"
    assert latest == _tab_newest(client, OWNER, "a1")


@pytest.mark.asyncio
async def test_a_mixed_message_list_fails_like_the_tab(env):
    """The tab keeps the rows before the first message it cannot read and drops
    the rest of that instance; the signal stops at the same place."""
    client, db, instance = env
    await db.insert("module_instances", {"instance_id": "mixed", "agent_id": "a1", "user_id": OWNER,
                                         "module_class": "ChatModule", "status": "active"})
    await db.insert("instance_json_format_memory_chat", {"instance_id": "mixed", "memory": json.dumps(
        {"messages": [_msg("assistant", "ok", "2026-10-30T10:00:00"), "junk",
                      _msg("assistant", "after", "2026-10-30T11:00:00")]})})
    assert _latest(client, OWNER, ["a1"]).get("a1") == _tab_newest(client, OWNER, "a1") == "2026-10-30T10:00:00"


@pytest.mark.asyncio
async def test_cache_eviction_during_a_call_drops_no_agent(env, monkeypatch):
    client, _db, instance = env
    monkeypatch.setattr(cl, "_LATEST_CACHE_MAX", 1)
    await instance("c1", "a1", OWNER, _msg("assistant", "x", "2026-10-30T10:00:00"))
    await instance("c2", "a2", OWNER, _msg("assistant", "y", "2026-10-30T11:00:00"))
    expected = {"a1": "2026-10-30T10:00:00", "a2": "2026-10-30T11:00:00"}
    assert _latest(client, OWNER, ["a1", "a2"]) == expected
    assert _latest(client, OWNER, ["a1", "a2"]) == expected


@pytest.mark.asyncio
async def test_more_rows_than_one_memory_read_chunk(env):
    client, _db, instance = env
    n = cl._MEMORY_READ_CHUNK + 5
    for i in range(n):
        await instance(f"c{i}", f"a{i}", OWNER, _msg("assistant", "x", f"2026-10-30T10:{i:02d}:00"))
    latest = _latest(client, OWNER, [f"a{i}" for i in range(n)])
    assert len(latest) == n
    assert latest[f"a{n - 1}"] == f"2026-10-30T10:{n - 1:02d}:00"


def test_unauthenticated_call_is_refused(env):
    client, _db, _instance = env
    r = client.get("/api/agents/chat-latest", params={"agent_ids": "a1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_input_bounds(env):
    client, _db, _instance = env
    assert _latest(client, OWNER, []) == {}
    too_many = [f"a{i}" for i in range(cl.CHAT_LATEST_MAX_AGENTS + 1)]
    r = client.get("/api/agents/chat-latest", params={"agent_ids": ",".join(too_many)},
                   headers={"x-test-user": OWNER})
    assert r.status_code == 422
