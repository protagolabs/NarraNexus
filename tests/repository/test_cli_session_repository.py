"""
@file_name: test_cli_session_repository.py
@author:
@date: 2026-07-24
@description: CRUD tests for CliSessionRepository against a REAL SQLite
database created by auto_migrate (the conftest `db_client` fixture; same
real-schema principle as tests/integration/test_cache_telemetry_chain_sqlite.py).
This is the test that catches a column-name mismatch between the
repository's row dicts and the agent_cli_sessions TableDef in schema_registry.
"""
import pytest
import pytest_asyncio

from xyz_agent_context.repository import CliSessionRepository
from xyz_agent_context.schema import AgentCliSession


@pytest_asyncio.fixture
async def repo(db_client):
    return CliSessionRepository(db_client)


def _handle(**overrides) -> AgentCliSession:
    base = dict(
        agent_id="agent_test_1",
        platform_session_id="sess_abcd1234",
        framework="claude_code",
        cli_session_id="cli_session_aaa",
        config_fingerprint="0123456789abcdef",
        working_path="/data/workspaces/u1/agent_test_1",
        narrative_id="nar_11111111",
    )
    base.update(overrides)
    return AgentCliSession(**base)


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(repo):
    assert await repo.get("agent_test_1", "sess_abcd1234", "claude_code") is None


@pytest.mark.asyncio
async def test_insert_then_get_roundtrip(repo):
    await repo.upsert(_handle())

    got = await repo.get("agent_test_1", "sess_abcd1234", "claude_code")
    assert got is not None
    assert got.cli_session_id == "cli_session_aaa"
    assert got.config_fingerprint == "0123456789abcdef"
    assert got.working_path == "/data/workspaces/u1/agent_test_1"
    assert got.narrative_id == "nar_11111111"
    # DB defaults filled the timestamps on insert.
    assert got.created_at is not None
    assert got.updated_at is not None


@pytest.mark.asyncio
async def test_upsert_overwrites_payload_keeping_one_row(repo, db_client):
    await repo.upsert(_handle())
    await repo.upsert(_handle(
        cli_session_id="cli_session_bbb",
        config_fingerprint="fedcba9876543210",
        working_path="/data/workspaces/u1/agent_test_1_moved",
        narrative_id="nar_22222222",
    ))

    rows = await db_client.get("agent_cli_sessions", {"agent_id": "agent_test_1"})
    assert len(rows) == 1

    got = await repo.get("agent_test_1", "sess_abcd1234", "claude_code")
    assert got is not None
    assert got.cli_session_id == "cli_session_bbb"
    assert got.config_fingerprint == "fedcba9876543210"
    assert got.working_path == "/data/workspaces/u1/agent_test_1_moved"
    assert got.narrative_id == "nar_22222222"


@pytest.mark.asyncio
async def test_key_triple_scopes_rows_independently(repo, db_client):
    await repo.upsert(_handle())
    await repo.upsert(_handle(framework="codex_cli", cli_session_id="cli_codex"))
    await repo.upsert(_handle(platform_session_id="sess_other000", cli_session_id="cli_other"))

    rows = await db_client.get("agent_cli_sessions", {"agent_id": "agent_test_1"})
    assert len(rows) == 3

    claude = await repo.get("agent_test_1", "sess_abcd1234", "claude_code")
    codex = await repo.get("agent_test_1", "sess_abcd1234", "codex_cli")
    assert claude is not None and claude.cli_session_id == "cli_session_aaa"
    assert codex is not None and codex.cli_session_id == "cli_codex"


@pytest.mark.asyncio
async def test_delete_handle_removes_only_the_key_triple(repo):
    await repo.upsert(_handle())
    await repo.upsert(_handle(framework="codex_cli", cli_session_id="cli_codex"))

    deleted = await repo.delete_handle("agent_test_1", "sess_abcd1234", "claude_code")
    assert deleted == 1
    assert await repo.get("agent_test_1", "sess_abcd1234", "claude_code") is None
    # The sibling framework row is untouched.
    assert await repo.get("agent_test_1", "sess_abcd1234", "codex_cli") is not None

    # Deleting an absent handle is a no-op, not an error.
    assert await repo.delete_handle("agent_test_1", "sess_abcd1234", "claude_code") == 0
