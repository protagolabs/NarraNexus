"""
@file_name: test_step4_cli_handle_persistence.py
@author:
@date: 2026-07-28
@description: step_4 [4.7] handle persistence (`_persist_cli_session_handle`)
against a REAL SQLite schema (conftest db_client). R3 contract: a
resume_failed run DELETES the stale row first — even when the cold retry
reported no new session id — then upserts the retry's fresh handle
(clear-old-write-new in one pass). Fail-open capture rules from R1 stay:
no half-rows, never raises.
"""
import importlib
from types import SimpleNamespace

import pytest

# The steps package re-exports the step_4 FUNCTION under the module's own
# name, so attribute-style imports hand back the function — resolve the
# actual module for monkeypatching.
step4_mod = importlib.import_module(
    "xyz_agent_context.agent_runtime._agent_runtime_steps.step_4_persist_results"
)
from xyz_agent_context.repository import CliSessionRepository
from xyz_agent_context.schema import AgentCliSession
from xyz_agent_context.schema.decision_schema import PathExecutionResult

AGENT = "agent_step4_test"
SESS = "sess_step4_1"
FPRINT = "0123456789abcdef"
CWD = "/data/workspaces/u1/agent_step4_test"


@pytest.fixture
def patch_db(monkeypatch, db_client):
    async def _get_db():
        return db_client

    monkeypatch.setattr(step4_mod, "get_db_client", _get_db)
    return db_client


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=AGENT,
        session=SimpleNamespace(session_id=SESS, current_narrative_id="nar_1"),
        substeps_4=[],
    )


def _result(**overrides) -> PathExecutionResult:
    base = dict(
        cli_session_id="cli_session_new",
        cli_framework="claude_code",
        cli_config_fingerprint=FPRINT,
        cli_working_path=CWD,
        resume_failed=False,
    )
    base.update(overrides)
    return PathExecutionResult(**base)


async def _seed_stale(db_client) -> None:
    await CliSessionRepository(db_client).upsert(AgentCliSession(
        agent_id=AGENT,
        platform_session_id=SESS,
        framework="claude_code",
        cli_session_id="cli_session_STALE",
        config_fingerprint=FPRINT,
        working_path=CWD,
        narrative_id="nar_1",
    ))


@pytest.mark.asyncio
async def test_normal_run_upserts_handle(patch_db):
    ctx = _ctx()
    await step4_mod._persist_cli_session_handle(ctx, _result())

    got = await CliSessionRepository(patch_db).get(AGENT, SESS, "claude_code")
    assert got is not None
    assert got.cli_session_id == "cli_session_new"
    assert any("[4.7]" in s for s in ctx.substeps_4)


@pytest.mark.asyncio
async def test_resume_failed_deletes_stale_then_upserts_new(patch_db):
    await _seed_stale(patch_db)
    ctx = _ctx()
    await step4_mod._persist_cli_session_handle(
        ctx, _result(cli_session_id="cli_session_retry", resume_failed=True)
    )

    rows = await patch_db.get("agent_cli_sessions", {"agent_id": AGENT})
    assert len(rows) == 1  # clear-old-write-new leaves exactly one row
    got = await CliSessionRepository(patch_db).get(AGENT, SESS, "claude_code")
    assert got is not None
    assert got.cli_session_id == "cli_session_retry"
    assert any("Stale CLI session handle cleared" in s for s in ctx.substeps_4)


@pytest.mark.asyncio
async def test_resume_failed_without_new_handle_still_deletes_stale(patch_db):
    # Cold retry didn't report a session id (e.g. it also produced no
    # ResultMessage) — the corpse must STILL be cleared so the next turn
    # doesn't re-trip over it.
    await _seed_stale(patch_db)
    ctx = _ctx()
    await step4_mod._persist_cli_session_handle(
        ctx, _result(cli_session_id=None, resume_failed=True)
    )

    assert await CliSessionRepository(patch_db).get(AGENT, SESS, "claude_code") is None


@pytest.mark.asyncio
async def test_missing_fingerprint_skips_upsert_but_never_raises(patch_db):
    ctx = _ctx()
    await step4_mod._persist_cli_session_handle(
        ctx, _result(cli_config_fingerprint=None)
    )
    assert await CliSessionRepository(patch_db).get(AGENT, SESS, "claude_code") is None


@pytest.mark.asyncio
async def test_no_session_is_a_noop(patch_db):
    ctx = _ctx()
    ctx.session = None
    await step4_mod._persist_cli_session_handle(ctx, _result(resume_failed=True))
    rows = await patch_db.get("agent_cli_sessions", {"agent_id": AGENT})
    assert rows == []
