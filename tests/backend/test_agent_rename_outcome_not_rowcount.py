"""
@file_name: test_agent_rename_outcome_not_rowcount.py
@author: NarraNexus
@date: 2026-08-17
@description: PUT /api/auth/agents/{id} must report the STORED OUTCOME, never
the driver's rowcount.

`AgentRepository.update_agent` returns `cursor.rowcount`, which counts MATCHED
rows on SQLite but CHANGED rows on MySQL. The route inferred failure from
`affected_rows > 0`, so on cloud (MySQL) a rename whose value was already
stored answered `success=False, error="No changes made"` for a row that holds
exactly what the caller asked for. The user reads that as "rename failed",
retries, and gets the same answer forever — while the DB has the new name all
along. `_awareness_writes.py` defused this same trap for the agent-facing tool
in 2026-08-05; this is the user-facing HTTP twin.

The tests force the MySQL reading of rowcount (return 0) because the SQLite
fixture returns 1 for a no-op write and would pass vacuously on the very
dialect the bug cannot occur on.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from xyz_agent_context.repository import AgentRepository
from xyz_agent_context.repository.user_repository import UserRepository

AGENT_ID = "agent_rename_test"
OWNER = "alice"


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


@pytest.fixture
def seeded(db_client):
    """One owner with one agent already named 小绿 (the post-rename state)."""

    async def _seed():
        users = UserRepository(db_client)
        await users.add_user(user_id=OWNER, user_type="individual")
        agents = AgentRepository(db_client)
        await agents.add_agent(
            agent_id=AGENT_ID,
            agent_name="小绿",
            agent_description="精通各地美食推荐",
            created_by=OWNER,
        )

    asyncio.get_event_loop().run_until_complete(_seed())


def _stored_name(db_client) -> str:
    async def _read():
        agent = await AgentRepository(db_client).get_agent(AGENT_ID)
        return agent.agent_name

    return asyncio.get_event_loop().run_until_complete(_read())


def test_resaving_the_stored_name_is_success_not_no_changes_made(
    client, db_client, seeded, monkeypatch
):
    """The desired state already holds → that is success, not a failure.

    On MySQL an identical re-save changes zero rows. Reporting that as an
    error is what made the tester retry a rename that had already landed.
    """
    calls: list[dict] = []

    async def _mysql_noop(self, agent_id, updates):  # noqa: ANN001
        calls.append(updates)
        return 0  # MySQL: CHANGED rows

    monkeypatch.setattr(AgentRepository, "update_agent", _mysql_noop)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿"},
        headers={"X-User-Id": OWNER},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True, body.get("error")
    assert body["agent"]["name"] == "小绿"
    # A value that already matches is not a write at all — the equality
    # short-circuit is what makes the answer dialect-independent.
    assert calls == [], f"no-op re-save still issued a write: {calls}"
    assert _stored_name(db_client) == "小绿"


def test_a_real_change_reported_as_zero_rows_still_succeeds(
    client, db_client, seeded, monkeypatch
):
    """rowcount is advisory; the re-read is the verdict.

    A genuine change whose driver reports 0 (collation-equal values, a pooled
    connection without CLIENT_FOUND_ROWS) must be judged by what the row now
    holds, not by the counter.
    """
    real_update = AgentRepository.update_agent

    async def _writes_but_reports_zero(self, agent_id, updates):  # noqa: ANN001
        await real_update(self, agent_id, updates)
        return 0

    monkeypatch.setattr(AgentRepository, "update_agent", _writes_but_reports_zero)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小蓝"},
        headers={"X-User-Id": OWNER},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True, body.get("error")
    assert body["agent"]["name"] == "小蓝"
    assert _stored_name(db_client) == "小蓝"


def test_a_write_that_truly_did_not_land_is_still_a_failure(
    client, db_client, seeded, monkeypatch
):
    """The guard must not become "always success".

    If the row does not hold what was asked after the write, the caller has to
    hear about it — that is the one case `success=False` exists for.
    """

    async def _swallows_the_write(self, agent_id, updates):  # noqa: ANN001
        return 0

    monkeypatch.setattr(AgentRepository, "update_agent", _swallows_the_write)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小蓝"},
        headers={"X-User-Id": OWNER},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert body["error"]
    assert _stored_name(db_client) == "小绿"


def test_rename_persists_and_is_visible_to_the_list_endpoint(
    client, db_client, seeded
):
    """The ticket's acceptance path: rename, then re-read the list.

    Runs against the unpatched repository so it also covers the ordinary
    SQLite path end to end.
    """
    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿2", "agent_description": "精通各地美食推荐"},
        headers={"X-User-Id": OWNER},
    )
    assert res.json()["success"] is True

    listed = client.get("/api/auth/agents", headers={"X-User-Id": OWNER}).json()
    assert listed["success"] is True
    names = {a["agent_id"]: a["name"] for a in listed["agents"]}
    assert names[AGENT_ID] == "小绿2"
