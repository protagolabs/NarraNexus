"""
@file_name: test_create_agent_empty_name_parity.py
@author: NarraNexus
@date: 2026-08-17
@description: Both create-agent paths refuse a blank name with the SAME string.

`create_agent` exists twice — the DirectStore seam (local) and the
`/social-network/create-agent` route (cloud) — and the pair is required to be
byte-identical, because the model reads whichever one its deployment happens to
use. `CREATE_AGENT_EMPTY_NAME_MSG` is shared for exactly that reason.

The gap this pins: the route's body model had `min_length=1`, so an empty name
was a 422 *before* the route's own check ran, and the model was handed a
transport failure string on one path and the shared constant on the other — for
the same tool call. Whitespace-only never had that problem (it passes
`min_length`, then normalizes to ""), so only the true-empty branch split.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from xyz_agent_context.module.social_network_module import (
    CREATE_AGENT_EMPTY_NAME_MSG,
)
from xyz_agent_context.repository import AgentRepository
from xyz_agent_context.repository.user_repository import UserRepository

OWNER = "alice"
CALLER = "agent_caller00"
NEW_ID = "agent_0123456789ab"

BLANK_NAMES = ["", "   "]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def seeded(db_client):
    async def _seed():
        await UserRepository(db_client).add_user(user_id=OWNER, user_type="individual")
        await AgentRepository(db_client).add_agent(
            agent_id=CALLER, agent_name="caller", created_by=OWNER
        )

    _run(_seed())


@pytest.fixture
def route_client(db_client, monkeypatch, seeded):
    import backend.routes.agents.social_network as sn

    async def _db():
        return db_client

    monkeypatch.setattr(sn, "get_db_client", _db)

    async def _allow(_request, _agent_id):
        return None

    monkeypatch.setattr(sn, "assert_owned", _allow)

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(sn.router, prefix="/api/agents")
    return TestClient(app)


@pytest.mark.parametrize("blank", BLANK_NAMES)
def test_the_route_refuses_with_the_shared_message(route_client, db_client, blank):
    res = route_client.post(
        f"/api/agents/{CALLER}/social-network/create-agent",
        json={"new_agent_id": NEW_ID, "agent_name": blank},
        headers={"X-User-Id": OWNER},
    )
    assert res.status_code == 200, (
        f"a blank name must reach the handler, not 422 out of the body model "
        f"({res.status_code}); otherwise the model gets a transport string "
        f"instead of {CREATE_AGENT_EMPTY_NAME_MSG!r}"
    )
    body = res.json()
    assert body["success"] is False
    assert body["error"] == CREATE_AGENT_EMPTY_NAME_MSG
    # Asserted HERE, in the test that actually triggered the refusal: the
    # db_client fixture is function-scoped, so a standalone "no row exists"
    # test would pass against a database nothing had touched. This one fails
    # if the check ever moves after provision_new_agent.
    assert _run(AgentRepository(db_client).get_agent(NEW_ID)) is None


@pytest.mark.parametrize("blank", BLANK_NAMES)
def test_the_direct_store_refuses_with_the_same_message(db_client, seeded, blank):
    from xyz_agent_context.module.data_access.store import DirectStore

    store = DirectStore()
    # DirectStore resolves its own db through the MCP factory; point it at the
    # test client so both twins run against the same rows.
    store._db = lambda: _async(db_client)  # type: ignore[method-assign]
    out = _run(
        store.create_agent(
            creator_agent_id=CALLER,
            new_agent_id=NEW_ID,
            agent_name=blank,
            awareness="",
            agent_description="",
        )
    )
    assert out["success"] is False
    assert out["message"] == CREATE_AGENT_EMPTY_NAME_MSG
    assert _run(AgentRepository(db_client).get_agent(NEW_ID)) is None


async def _async(value):
    return value
