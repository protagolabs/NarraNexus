"""
@file_name: test_rename_records_identity_everywhere.py
@author: NarraNexus
@date: 2026-08-18
@description: EVERY writer of ``agents.agent_name`` must record the rename in
the Awareness profile — not just the agent-facing tool.

Shenzhen round 2, P1 (prod agent_4a0ae5f40af2, retest 2026-08-14 by
xiyue.liang). The agent was created 「小绿」, renamed to 「美食家」 through its own
``update_agent_profile`` tool — which appended the identity-correction line the
2026-08-04 transaction was built for — and then renamed BACK to 「小绿」 from the
UI. The HTTP route wrote the column and refreshed the peer directory, but wrote
no note, so the profile kept asserting, in the platform's own voice:

    - 2026-08-14: renamed ... to 「美食家」. You are 「美食家」.
      「小绿」 is no longer your name — ...

``agents.agent_name`` said 小绿 and ``bus_agent_registry`` said 小绿, while the
system prompt carried a platform record instructing the agent to REJECT 小绿.
Asked "你是谁", it answered 美食家 — twice, and with justification. This is not
memory that failed to keep up; it is the platform telling the agent something
false, so a test that only renames a fresh agent would miss it: the note must
be SUPERSEDED, not merely present.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from xyz_agent_context.module.awareness_module import IDENTITY_CHANGE_SECTION

OWNER = "owner_shenzhen"
AGENT_ID = "agent_4a0ae5f40af2"
INSTANCE_ID = "aware_d571b048"

# The profile as prod actually held it at 2026-08-14 15:48 (probe, read-only):
# a self-description line naming the agent, plus a correction pointing the
# WRONG way once the UI renamed it back. Typed-from-memory fixtures are how a
# suite stays green against a shape upstream never produces.
STALE_PROFILE = (
    "# Agent Awareness Profile\n"
    "\n"
    "## 4. Role and Identity\n"
    "- 名称：美食家；精通各地美食推荐，可为用户推荐各地特色美食、餐厅与地道吃法\n"
    "\n"
    f"{IDENTITY_CHANGE_SECTION}\n"
    "- 2026-08-14: renamed by your creator from 「小绿」 to 「美食家」. "
    "You are 「美食家」. 「小绿」 is no longer your name — if it appears in your "
    "memories or past conversations, that is history, and it may now belong "
    "to a different agent.\n"
    "\n"
    "## 5. Owner observations\n"
    "- owner prefers concise answers\n"
)


async def _async_return(value):
    return value


async def _seed(db, *, name: str, profile: str) -> None:
    await db.insert("agents", {
        "agent_id": AGENT_ID, "agent_name": name, "created_by": OWNER,
        "agent_description": "精通各地美食推荐", "is_public": 0,
    })
    await db.insert("module_instances", {
        "instance_id": INSTANCE_ID, "agent_id": AGENT_ID, "user_id": OWNER,
        "module_class": "AwarenessModule", "status": "active",
    })
    await db.insert("instance_awareness", {
        "instance_id": INSTANCE_ID, "awareness": profile,
    })


async def _profile(db) -> str:
    row = await db.get_one("instance_awareness", {"instance_id": INSTANCE_ID})
    return (row or {})["awareness"]


def _identity_entries(profile: str) -> list[str]:
    """The bullet lines of the identity section, in order."""
    _, _, rest = profile.partition(IDENTITY_CHANGE_SECTION)
    lines = rest.splitlines()
    cut = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("## ")),
        len(lines),
    )
    return [ln.strip() for ln in lines[:cut] if ln.strip().startswith("- ")]


@pytest.fixture
def ui_client(db_client, monkeypatch):
    """PUT /api/auth/agents/{id} — what the frontend rename button calls."""
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
def manyfold_client(db_client, monkeypatch):
    """PATCH /manyfold/agents/{id} — the Manyfold-initiated rename."""
    import backend.routes.manyfold.agents as mf_mod

    monkeypatch.setattr(mf_mod, "get_db_client", lambda: _async_return(db_client))
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(mf_mod.router)
    return TestClient(app)


@pytest.mark.asyncio
async def test_ui_rename_supersedes_a_note_pointing_the_other_way(
    db_client, ui_client
):
    """The incident itself: rename back, and the platform record must follow.

    Asserts the LAST entry, not merely that 小绿 appears somewhere — the stale
    line already names 小绿 (as the retired name), so `"小绿" in profile` passes
    on the buggy code and proves nothing.
    """
    await _seed(db_client, name="美食家", profile=STALE_PROFILE)

    resp = ui_client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿"},
        headers={"X-User-Id": OWNER},
    )
    assert resp.status_code == 200 and resp.json()["success"] is True

    entries = _identity_entries(await _profile(db_client))
    assert len(entries) == 2, f"expected the rename to be appended, got {entries}"
    latest = entries[-1]
    assert "「小绿」" in latest and "「美食家」" in latest
    assert "You are 「小绿」" in latest
    assert "「美食家」 is no longer your name" in latest


@pytest.mark.asyncio
async def test_ui_rename_keeps_the_rest_of_the_profile(db_client, ui_client):
    """The owner observations below the section are not ours to edit."""
    await _seed(db_client, name="美食家", profile=STALE_PROFILE)

    ui_client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿"},
        headers={"X-User-Id": OWNER},
    )

    profile = await _profile(db_client)
    assert "## 5. Owner observations" in profile
    assert "owner prefers concise answers" in profile


@pytest.mark.asyncio
async def test_ui_description_only_edit_records_no_rename(db_client, ui_client):
    """Only a NAME change is an identity change — no note for a description."""
    await _seed(db_client, name="小绿", profile="# Agent Awareness Profile\n")

    ui_client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_description": "只推荐深圳本地菜"},
        headers={"X-User-Id": OWNER},
    )

    assert IDENTITY_CHANGE_SECTION not in await _profile(db_client)


@pytest.mark.asyncio
async def test_manyfold_rename_records_the_identity_change(
    db_client, manyfold_client
):
    await _seed(db_client, name="美食家", profile=STALE_PROFILE)

    resp = manyfold_client.patch(
        f"/manyfold/agents/{AGENT_ID}", json={"agent_name": "小绿"}
    )
    assert resp.status_code == 200

    latest = _identity_entries(await _profile(db_client))[-1]
    assert "You are 「小绿」" in latest


@pytest.mark.asyncio
async def test_manyfold_rename_refreshes_the_peer_directory(
    db_client, manyfold_client
):
    """Manyfold was the one rename path that never touched discovery at all."""
    await _seed(db_client, name="美食家", profile=STALE_PROFILE)

    manyfold_client.patch(
        f"/manyfold/agents/{AGENT_ID}", json={"agent_name": "小绿"}
    )

    row = await db_client.get_one("bus_agent_registry", {"agent_id": AGENT_ID})
    assert row is not None, "Manyfold rename left the agent out of the directory"
    assert row["description"].startswith("小绿")
