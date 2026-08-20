"""
@file_name: test_event_log_meta.py
@author: Bin Liang
@date: 2026-07-23
@description: Tests for the run-level `meta` block of
GET /api/agents/{agent_id}/event-log/{event_id}.

The activity ("inner thought") card needs more than the loop timeline:
what input the agent received and from where, what it produced, when the
run started, how long it took, what it cost and on which models
(bug tracker: "Agent 内心活动显示优化"). All of that already exists in the
events row + cost_records — this endpoint now surfaces it as `meta`.

Covers:
- trigger/trigger_source/input_text (env_context.input) round-trip
- started_at/finished_at/duration_seconds/state
- cost aggregation across cost_records rows (models, cost, tokens)
- graceful nulls: legacy row without lifecycle timestamps / costs
- input_text is capped so a huge bus payload can't bloat the response
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.schema_registry import auto_migrate

import backend.routes.agents.chat_history as hist_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _restore_get_db():
    import xyz_agent_context.utils.db.db_factory as db_factory_mod
    original_factory = db_factory_mod.get_db_client
    original_mod = hist_mod.get_db_client
    yield
    db_factory_mod.get_db_client = original_factory
    hist_mod.get_db_client = original_mod


def _build_client(db_client):
    app = FastAPI()
    app.include_router(hist_mod.router, prefix="/api/agents")

    async def _get_db_override():
        return db_client

    import xyz_agent_context.utils.db.db_factory as db_factory_mod

    db_factory_mod.get_db_client = _get_db_override
    hist_mod.get_db_client = _get_db_override
    return TestClient(app)


async def _seed_event(db, *, event_id="evt_meta1", agent_id="agent_a", **overrides):
    row = {
        "event_id": event_id,
        "trigger": "job",
        "trigger_source": "job",
        "agent_id": agent_id,
        "user_id": "user_x",
        "env_context": json.dumps({"input": "Run the daily briefing for markets"}),
        "event_log": json.dumps([
            {"content": {"type": "thinking", "content": "planning"}},
            {"content": {"type": "tool_call", "tool_name": "web_search", "arguments": {"q": "spx"}}},
            {"content": {"type": "tool_output", "output": "ok"}},
        ]),
        "final_output": "Briefing sent to the user.",
        "state": "completed",
        "started_at": "2026-07-23 08:00:00",
        "finished_at": "2026-07-23 08:01:30",
    }
    row.update(overrides)
    await db.insert("events", row)


@pytest.mark.asyncio
async def test_meta_carries_input_lifecycle_and_costs(db_client):
    await _seed_event(db_client)
    for model, itok, otok, cost in (
        ("deepseek-v4", 1200, 300, 0.004),
        ("bge-m3", 50, 0, 0.0001),
    ):
        await db_client.insert("cost_records", {
            "agent_id": "agent_a", "event_id": "evt_meta1",
            "call_type": "agent_loop", "model": model,
            "input_tokens": itok, "output_tokens": otok,
            "total_cost_usd": cost,
        })

    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_meta1").json()

    assert body["success"] is True
    meta = body["meta"]
    assert meta["trigger_source"] == "job"
    assert meta["input_text"] == "Run the daily briefing for markets"
    assert meta["final_output"] == "Briefing sent to the user."
    assert meta["state"] == "completed"
    assert meta["duration_seconds"] == 90.0
    assert sorted(meta["models"]) == ["bge-m3", "deepseek-v4"]
    assert meta["input_tokens"] == 1250
    assert meta["output_tokens"] == 300
    assert abs(meta["total_cost_usd"] - 0.0041) < 1e-9
    assert meta["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_meta_includes_cache_buckets(db_client):
    """input_tokens is only the full-rate bucket; the cache columns carry the
    bulk on a cache-warm run and must be surfaced separately, not dropped."""
    await _seed_event(db_client, event_id="evt_cache")
    await db_client.insert("cost_records", {
        "agent_id": "agent_a", "event_id": "evt_cache",
        "call_type": "agent_loop", "model": "claude-code",
        "input_tokens": 33, "output_tokens": 19_528,
        "cache_read_input_tokens": 735_147,
        "cache_creation_input_tokens": 134_071,
        "total_cost_usd": 2.198,
    })

    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_cache").json()

    meta = body["meta"]
    assert meta["input_tokens"] == 33
    assert meta["cache_read_tokens"] == 735_147
    assert meta["cache_creation_tokens"] == 134_071


@pytest.mark.asyncio
async def test_meta_graceful_on_legacy_row_without_lifecycle_or_costs(db_client):
    await _seed_event(
        db_client, event_id="evt_old",
        started_at=None, finished_at=None, env_context=None,
    )

    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_old").json()

    assert body["success"] is True
    meta = body["meta"]
    assert meta["input_text"] is None
    assert meta["duration_seconds"] is None
    assert meta["models"] == []
    assert meta["total_cost_usd"] is None


@pytest.mark.asyncio
async def test_meta_input_text_is_capped(db_client):
    await _seed_event(
        db_client, event_id="evt_big",
        env_context=json.dumps({"input": "x" * 20000}),
    )

    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_big").json()

    assert len(body["meta"]["input_text"]) <= 4000


@pytest.mark.asyncio
async def test_timeline_tool_output_inherits_call_name(db_client):
    """Stored tool_output entries carry no tool_name; the timeline must give
    them the preceding call's name — never a literal placeholder. A reverted
    fix shows "[output] unknown" on every row of the disclosure."""
    await _seed_event(db_client, event_id="evt_names")
    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_names").json()

    assert body["success"] is True
    timeline = body["timeline"]
    outputs = [e for e in timeline if e["type"] == "tool_output"]
    assert outputs and outputs[0]["tool_name"] == "web_search"
    assert "unknown" not in json.dumps(timeline)


@pytest.mark.asyncio
async def test_timeline_parallel_outputs_pair_by_call_id(db_client):
    """Parallel tool calls: every call lands before any output and the
    outputs return in completion order. "Nearest preceding call" would
    confidently attach the WRONG name — pairing must go by tool_call_id,
    in both the timeline and the grouped tool_calls view."""
    await _seed_event(
        db_client,
        event_id="evt_parallel",
        event_log=json.dumps([
            {"content": {"type": "tool_call", "tool_call_id": "id1",
                         "tool_name": "read_file", "arguments": {"path": "a"}}},
            {"content": {"type": "tool_call", "tool_call_id": "id2",
                         "tool_name": "web_search", "arguments": {"q": "spx"}}},
            {"content": {"type": "tool_output", "tool_call_id": "id2",
                         "output": "search results"}},
            {"content": {"type": "tool_output", "tool_call_id": "id1",
                         "output": "file body"}},
        ]),
    )
    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_parallel").json()
    assert body["success"] is True

    outputs = [e for e in body["timeline"] if e["type"] == "tool_output"]
    assert [(o["tool_name"], o["tool_output"]) for o in outputs] == [
        ("web_search", "search results"),
        ("read_file", "file body"),
    ]

    calls = {c["tool_name"]: c["tool_output"] for c in body["tool_calls"]}
    assert calls == {"read_file": "file body", "web_search": "search results"}


@pytest.mark.asyncio
async def test_timeline_empty_named_call_does_not_borrow_sibling_name(db_client):
    """A persisted call whose name is KNOWN-empty (the writer refuses to
    invent placeholders) must keep its output unnamed — inheriting a
    parallel sibling's name would be a confidently wrong label."""
    await _seed_event(
        db_client,
        event_id="evt_emptyname",
        event_log=json.dumps([
            {"content": {"type": "tool_call", "tool_call_id": "id1",
                         "tool_name": "", "arguments": {}}},
            {"content": {"type": "tool_call", "tool_call_id": "id2",
                         "tool_name": "web_search", "arguments": {"q": "x"}}},
            {"content": {"type": "tool_output", "tool_call_id": "id1",
                         "output": "anon result"}},
        ]),
    )
    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_emptyname").json()
    outputs = {e["tool_output"]: e["tool_name"] for e in body["timeline"] if e["type"] == "tool_output"}
    assert outputs["anon result"] == ""
    # BOTH views must agree — they read one shared index now, and a drift
    # would make the endpoint contradict itself.
    grouped = {c["tool_name"]: c["tool_output"] for c in body["tool_calls"]}
    assert grouped == {"": "anon result", "web_search": None}


@pytest.mark.asyncio
async def test_timeline_output_with_unseen_id_stays_unnamed(db_client):
    """An output carrying an id we never saw a call for: we OUGHT to know
    the owner and genuinely don't — an honest blank, never a sibling's
    name."""
    await _seed_event(
        db_client,
        event_id="evt_ghostid",
        event_log=json.dumps([
            {"content": {"type": "tool_call", "tool_call_id": "id1",
                         "tool_name": "web_search", "arguments": {"q": "x"}}},
            {"content": {"type": "tool_output", "tool_call_id": "ghost",
                         "output": "orphan"}},
            {"content": {"type": "tool_output", "tool_call_id": "id1",
                         "output": "results"}},
        ]),
    )
    client = _build_client(db_client)
    body = client.get("/api/agents/agent_a/event-log/evt_ghostid").json()
    outputs = {e["tool_output"]: e["tool_name"] for e in body["timeline"] if e["type"] == "tool_output"}
    assert outputs == {"orphan": "", "results": "web_search"}

