"""
@file_name: test_manyfold_workspace_materialize.py
@author: NarraNexus
@date: 2026-08-14
@description: POST /manyfold/agents must materialize the agent workspace dir.

Manyfold #832: the gateway create wrote the `users` + `agents` rows but never
created the workspace directory, so the path
`GET /manyfold/agents/{id}/files/roots` resolves and returns did not exist on
disk, and the Manyfold runner's `workspace.ensure(create=false)` (correctly)
refused to start the sandbox. The runner must not create arbitrary framework
paths — the owner is this side, so the create contract only returns once its
own canonical workspace exists.

Covered: first create, concurrent creates, same-user sibling isolation,
cross-owner conflicts, idempotent replay repairing a deleted workspace, the
failure surfacing as a non-leaking 5xx instead of a false success, an unsafe id
rejected before any mkdir, and the create → roots round trip through the
gateway routes.
"""
from __future__ import annotations

import asyncio
import shutil

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

import backend.routes.manyfold.agents as agents_mod
import backend.routes.manyfold.files as files_mod
from xyz_agent_context.settings import settings as core_settings
from xyz_agent_context.utils.workspace_paths import agent_workspace_path


@pytest.fixture
def workspace_base(tmp_path, monkeypatch):
    """Point `base_working_path` at a scratch dir for the whole test."""
    base = tmp_path / "workspaces"
    base.mkdir()
    monkeypatch.setattr(core_settings, "base_working_path", str(base))
    return base


@pytest.fixture
def gateway_app(db_client, monkeypatch):
    """Both gateway routers behind an already-authed middleware.

    `files` rides along so the acceptance path (create → roots) exercises the
    real endpoint pair rather than asserting on the create side alone.
    """

    async def fake_db():
        return db_client

    monkeypatch.setattr(agents_mod, "get_db_client", fake_db)
    monkeypatch.setattr(files_mod, "get_db_client", fake_db)

    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(agents_mod.router)
    app.include_router(files_mod.router)
    return app


async def _create(app, agent_id: str, manyfold_user_id: str = "alice", **extra):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post(
            "/manyfold/agents",
            json={
                "agent_id": agent_id,
                "manyfold_user_id": manyfold_user_id,
                **extra,
            },
        )


async def _roots(app, agent_id: str):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(f"/manyfold/agents/{agent_id}/files/roots")


def _expected(base, agent_id: str, user_id: str = "mf_alice"):
    return agent_workspace_path(agent_id, user_id, base=str(base))


# ---------------------------------------------------------------------------
# First create — the #832 regression itself
# ---------------------------------------------------------------------------


async def test_first_create_materializes_the_workspace(gateway_app, workspace_base):
    resp = await _create(gateway_app, "agent_aaa11111")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_created"] is True
    assert body["user_created"] is True

    ws = _expected(workspace_base, "agent_aaa11111")
    assert ws.is_dir(), f"create returned success but {ws} does not exist"
    # The response names the directory it just guaranteed, so the caller never
    # has to re-derive the layout (Manyfold's seed path is a guess by design).
    assert body["workspace"] == str(ws)


async def test_create_reports_a_workspace_the_roots_endpoint_can_serve(
    gateway_app, workspace_base
):
    """Gateway integration: the path `files/roots` hands back exists on disk.

    This is the exact pair the runner walks — create, read the root, then
    `ensure(create=false)`.
    """
    await _create(gateway_app, "agent_bbb22222")

    resp = await _roots(gateway_app, "agent_bbb22222")
    assert resp.status_code == 200, resp.text
    roots = resp.json()["roots"]
    assert len(roots) == 1

    from pathlib import Path

    served = Path(roots[0]["path"])
    assert served == _expected(workspace_base, "agent_bbb22222")
    assert served.is_dir(), "roots served a path the runner cannot ensure()"


# ---------------------------------------------------------------------------
# Second agent, same user — isolation
# ---------------------------------------------------------------------------


async def test_second_agent_for_same_user_gets_an_isolated_workspace(
    gateway_app, workspace_base
):
    await _create(gateway_app, "agent_ccc33333")
    await _create(gateway_app, "agent_ddd44444")

    first = _expected(workspace_base, "agent_ccc33333")
    second = _expected(workspace_base, "agent_ddd44444")

    assert first.is_dir() and second.is_dir()
    assert first != second
    # Siblings under the one per-user root, never nested inside each other:
    # that containment is what keeps one agent's files out of the other's
    # tree (and out of its `files/list`).
    assert first.parent == second.parent == workspace_base / "mf_alice"
    assert not first.is_relative_to(second)
    assert not second.is_relative_to(first)

    (first / "secret.txt").write_text("first agent only", encoding="utf-8")
    assert [p.name for p in second.iterdir()] == []


async def test_two_concurrent_agents_for_one_user_share_one_user_row(
    gateway_app, workspace_base, db_client
):
    responses = await asyncio.gather(
        _create(gateway_app, "agent_conc0001"),
        _create(gateway_app, "agent_conc0002"),
    )

    assert [response.status_code for response in responses] == [200, 200]
    assert len(await db_client.get("users", {"user_id": "mf_alice"})) == 1
    assert len(await db_client.get("agents", {"created_by": "mf_alice"})) == 2
    assert _expected(workspace_base, "agent_conc0001").is_dir()
    assert _expected(workspace_base, "agent_conc0002").is_dir()


async def test_two_concurrent_creates_of_one_agent_are_idempotent(
    gateway_app, workspace_base, db_client
):
    responses = await asyncio.gather(
        _create(gateway_app, "agent_conc0003"),
        _create(gateway_app, "agent_conc0003"),
    )

    assert [response.status_code for response in responses] == [200, 200]
    bodies = [response.json() for response in responses]
    assert sorted(body["agent_created"] for body in bodies) == [False, True]
    assert len(await db_client.get("users", {"user_id": "mf_alice"})) == 1
    assert len(await db_client.get("agents", {"agent_id": "agent_conc0003"})) == 1
    workspace = _expected(workspace_base, "agent_conc0003")
    assert workspace.is_dir()
    assert {body["workspace"] for body in bodies} == {str(workspace)}


# ---------------------------------------------------------------------------
# Ownership is immutable through create
# ---------------------------------------------------------------------------


async def test_existing_agent_cannot_be_reassigned_to_another_user(
    gateway_app, workspace_base, db_client
):
    agent_id = "agent_owner001"
    first = await _create(gateway_app, agent_id, manyfold_user_id="alice")
    assert first.status_code == 200, first.text
    alice_workspace = _expected(workspace_base, agent_id, user_id="mf_alice")
    (alice_workspace / "owner.txt").write_text("alice", encoding="utf-8")

    conflict = await _create(gateway_app, agent_id, manyfold_user_id="bob")

    assert conflict.status_code == 409, conflict.text
    row = await db_client.get_one("agents", {"agent_id": agent_id})
    assert row["created_by"] == "mf_alice"
    assert (alice_workspace / "owner.txt").read_text(encoding="utf-8") == "alice"
    assert not _expected(workspace_base, agent_id, user_id="mf_bob").exists()
    assert await db_client.get_one("users", {"user_id": "mf_bob"}) is None


async def test_concurrent_different_owner_claims_have_one_winner(
    gateway_app, workspace_base, db_client
):
    agent_id = "agent_owner002"
    responses = await asyncio.gather(
        _create(gateway_app, agent_id, manyfold_user_id="alice"),
        _create(gateway_app, agent_id, manyfold_user_id="bob"),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    winner = success.json()["user_id"]
    loser = "mf_bob" if winner == "mf_alice" else "mf_alice"
    row = await db_client.get_one("agents", {"agent_id": agent_id})
    assert row["created_by"] == winner
    assert _expected(workspace_base, agent_id, user_id=winner).is_dir()
    assert not _expected(workspace_base, agent_id, user_id=loser).exists()


# ---------------------------------------------------------------------------
# Idempotent replay repairs a missing workspace
# ---------------------------------------------------------------------------


async def test_replay_repairs_a_deleted_workspace(gateway_app, workspace_base):
    await _create(gateway_app, "agent_eee55555")
    ws = _expected(workspace_base, "agent_eee55555")
    shutil.rmtree(ws)
    assert not ws.exists()

    resp = await _create(gateway_app, "agent_eee55555")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Rows already existed — this is the update leg, and it still repairs.
    assert body["agent_created"] is False
    assert body["user_created"] is False
    assert ws.is_dir()


async def test_replay_keeps_existing_workspace_contents(gateway_app, workspace_base):
    await _create(gateway_app, "agent_fff66666")
    ws = _expected(workspace_base, "agent_fff66666")
    (ws / "Bootstrap.md").write_text("keep me", encoding="utf-8")

    resp = await _create(gateway_app, "agent_fff66666")

    assert resp.status_code == 200, resp.text
    assert (ws / "Bootstrap.md").read_text(encoding="utf-8") == "keep me"


# ---------------------------------------------------------------------------
# Failure semantics — no false success
# ---------------------------------------------------------------------------


async def test_unmaterializable_workspace_fails_the_create(
    gateway_app, tmp_path, monkeypatch, db_client
):
    """A base that cannot hold the dir must surface, not return 200.

    `blocker` is a regular file, so creating `<blocker>/workspaces/...` raises
    NotADirectoryError from the real filesystem — no mocking needed.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(core_settings, "base_working_path", str(blocker / "workspaces"))

    resp = await _create(gateway_app, "agent_ggg77777")

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == "failed to materialize agent workspace"
    assert str(blocker) not in resp.text
    assert "NotADirectoryError" not in resp.text

    # The DB claim is deliberately retained so a replay can repair the
    # filesystem without guessing which rows are safe to roll back.
    row = await db_client.get_one("agents", {"agent_id": "agent_ggg77777"})
    assert row["created_by"] == "mf_alice"
    repair_base = tmp_path / "repair-workspaces"
    monkeypatch.setattr(core_settings, "base_working_path", str(repair_base))
    replay = await _create(gateway_app, "agent_ggg77777")
    assert replay.status_code == 200, replay.text
    assert replay.json()["agent_created"] is False
    assert _expected(repair_base, "agent_ggg77777").is_dir()


async def test_unsafe_agent_id_is_rejected_before_any_mkdir(
    gateway_app, workspace_base, db_client
):
    """The id is a path segment here — a traversing value must not dig.

    Rejected before the rows, too: a create that 400s must not leave a
    half-provisioned agent behind.
    """
    resp = await _create(gateway_app, "../escapee")

    assert resp.status_code == 400, resp.text
    assert not (workspace_base.parent / "escapee").exists()
    assert list(workspace_base.glob("**/escapee")) == []
    assert await db_client.get("agents", {"agent_id": "../escapee"}) == []
    assert await db_client.get("users", {"user_id": "mf_alice"}) == []


async def test_user_id_that_normalizes_empty_is_a_client_error(
    gateway_app, workspace_base, db_client
):
    resp = await _create(
        gateway_app,
        "agent_hhh88888",
        manyfold_user_id="   ",
    )

    assert resp.status_code == 400, resp.text
    assert await db_client.get("agents", {}) == []
    assert await db_client.get("users", {}) == []
    assert list(workspace_base.iterdir()) == []
