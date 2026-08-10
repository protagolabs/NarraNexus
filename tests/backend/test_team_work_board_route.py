"""
@file_name: test_team_work_board_route.py
@author:
@date: 2026-08-10
@description: The user's half of the work board — see it, and un-park it.

Read-and-resume only: agents maintain the board through their tools, and a
board the user also edits by hand would drift from the one the lead is held
responsible for.

The load-bearing difference from the agent-facing view is `paused`. That is
the state a stop leaves behind, and it is the USER who decides whether to
resume — so hiding it (as the agent's list does) would make a stopped task
look deleted.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.team_work_repository import TeamWorkItemRepository
from xyz_agent_context.schema.team_work_schema import WorkItemStatus
from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.schema_registry import auto_migrate

import backend.routes.teams as teams_mod


@pytest_asyncio.fixture
async def db_client():
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


def _client(db_client, monkeypatch, viewer="usr_owner"):
    app = FastAPI()
    app.include_router(teams_mod.router, prefix="/api/teams")

    @app.middleware("http")
    async def _auth(request, call_next):
        request.state.user_id = viewer
        return await call_next(request)

    async def _get_db():
        return db_client

    monkeypatch.setattr(teams_mod, "get_db_client", _get_db)
    return TestClient(app)


async def _seed(db, *, owner="usr_owner", lead="agent_lead"):
    await db.insert("teams", {
        "team_id": "t1", "owner_user_id": owner, "name": "Desk",
        "lead_agent_id": lead or None,
    })
    await db.insert("team_members", {"team_id": "t1", "agent_id": "agent_worker"})
    await db.insert("agents", {"agent_id": "agent_worker", "agent_name": "Bruno",
                               "created_by": owner})
    return TeamWorkItemRepository(db)


@pytest.mark.asyncio
async def test_the_board_shows_parked_items_too(db_client, monkeypatch):
    repo = await _seed(db_client)
    live = await repo.create_item(team_id="t1", channel_id="ch", title="live",
                                  created_by="agent_lead", assignee_id="agent_worker")
    parked = await repo.create_item(team_id="t1", channel_id="ch", title="parked",
                                    created_by="agent_lead", root_run_id="evt_r")
    await repo.pause_by_root("evt_r")
    done = await repo.create_item(team_id="t1", channel_id="ch", title="done",
                                  created_by="agent_lead")
    await repo.set_status(done.item_id, WorkItemStatus.DONE)

    r = _client(db_client, monkeypatch).get("/api/teams/t1/work-items")

    assert r.status_code == 200
    titles = {i["title"]: i["status"] for i in r.json()["items"]}
    # Parked is visible — a stopped task must not look deleted.
    assert titles == {"live": WorkItemStatus.IN_PROGRESS, "parked": WorkItemStatus.PAUSED}
    del live, parked
    # Assignee resolves to a display name so the panel need not join again.
    assert [i["assignee_name"] for i in r.json()["items"] if i["title"] == "live"] == ["Bruno"]


@pytest.mark.asyncio
async def test_resuming_returns_it_to_its_owner(db_client, monkeypatch):
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead", assignee_id="agent_worker",
                                  root_run_id="evt_r")
    await repo.pause_by_root("evt_r")

    r = _client(db_client, monkeypatch).post(
        f"/api/teams/t1/work-items/{item.item_id}/resume"
    )

    assert r.status_code == 200
    assert (await repo.get(item.item_id)).status == WorkItemStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_resuming_an_unclaimed_item_returns_it_to_the_pool(db_client, monkeypatch):
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead", root_run_id="evt_r")
    await repo.pause_by_root("evt_r")

    _client(db_client, monkeypatch).post(f"/api/teams/t1/work-items/{item.item_id}/resume")

    assert (await repo.get(item.item_id)).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_resuming_something_already_live_is_not_an_error(db_client, monkeypatch):
    """The user clicked resume on a task that came back on its own."""
    repo = await _seed(db_client)
    item = await repo.create_item(team_id="t1", channel_id="ch", title="OCR",
                                  created_by="agent_lead")

    r = _client(db_client, monkeypatch).post(
        f"/api/teams/t1/work-items/{item.item_id}/resume"
    )

    assert r.status_code == 200
    assert (await repo.get(item.item_id)).status == WorkItemStatus.OPEN


@pytest.mark.asyncio
async def test_a_non_owner_sees_nothing(db_client, monkeypatch):
    repo = await _seed(db_client, owner="usr_owner")
    await repo.create_item(team_id="t1", channel_id="ch", title="secret",
                           created_by="agent_lead")

    r = _client(db_client, monkeypatch, viewer="usr_intruder").get("/api/teams/t1/work-items")

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_patrol_can_be_switched_off_and_reported(db_client, monkeypatch):
    await _seed(db_client)
    client = _client(db_client, monkeypatch)

    # A team with a lead defaults to ON even though the column is NULL.
    assert client.get("/api/teams/t1/work-items").json()["patrol_enabled"] is True

    assert client.put("/api/teams/t1/patrol", json={"enabled": False}).status_code == 200
    assert client.get("/api/teams/t1/work-items").json()["patrol_enabled"] is False


@pytest.mark.asyncio
async def test_a_team_without_a_lead_reports_patrol_off(db_client, monkeypatch):
    """No lead = nobody responsible, so there is nothing to switch on."""
    await _seed(db_client, lead="")

    r = _client(db_client, monkeypatch).get("/api/teams/t1/work-items")

    assert r.json()["patrol_enabled"] is False
