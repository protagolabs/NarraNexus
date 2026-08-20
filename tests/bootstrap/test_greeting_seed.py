"""
@file_name: test_greeting_seed.py
@author: Bin Liang
@date: 2026-08-20
@description: Lock resolve_bootstrap_greeting_to_seed + _bootstrap_active — the
"should this agent be seeded" decision. The gate MUST match the hook's
bootstrap_active (not just "greeting in metadata"), or the greeting is re-seeded
into every new narrative the agent ever opens. The chat-row write / ordering are
locked in tests/chat_module/test_chat_writes.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.bootstrap import greeting_seed


def _agent_repo_returning(agent):
    class _Repo:
        def __init__(self, db):
            pass

        async def get_agent(self, agent_id):
            return agent

    return _Repo


def _agent(metadata, created_by="u1"):
    return SimpleNamespace(agent_metadata=metadata, created_by=created_by)


# ---- resolve_bootstrap_greeting_to_seed ---------------------------------


@pytest.mark.asyncio
async def test_returns_greeting_when_owner_bootstrapping(monkeypatch):
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "Hi, I'm Echo!"})),
    )
    monkeypatch.setattr(greeting_seed, "_bootstrap_active", AsyncMock(return_value=True))

    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got == "Hi, I'm Echo!"


@pytest.mark.asyncio
async def test_none_when_bootstrap_expired(monkeypatch):
    """Greeting metadata is permanent; the gate must expire with bootstrap_active
    so a post-auto-delete agent is never re-greeted on a new narrative."""
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "Hi!"})),
    )
    monkeypatch.setattr(greeting_seed, "_bootstrap_active", AsyncMock(return_value=False))

    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None


@pytest.mark.asyncio
async def test_none_when_not_owner(monkeypatch):
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(
            _agent({"bootstrap_greeting": "Hi!"}, created_by="someone_else")
        ),
    )
    active = AsyncMock(return_value=True)
    monkeypatch.setattr(greeting_seed, "_bootstrap_active", active)

    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None
    active.assert_not_awaited()  # short-circuits before the expensive check


@pytest.mark.asyncio
async def test_none_when_no_greeting(monkeypatch):
    monkeypatch.setattr(
        greeting_seed, "AgentRepository", _agent_repo_returning(_agent({}))
    )
    monkeypatch.setattr(greeting_seed, "_bootstrap_active", AsyncMock(return_value=True))
    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None


@pytest.mark.asyncio
async def test_none_when_agent_missing(monkeypatch):
    monkeypatch.setattr(
        greeting_seed, "AgentRepository", _agent_repo_returning(None)
    )
    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None


# ---- _bootstrap_active (Bootstrap.md + event_count threshold) -----------


def _db_with_count(n):
    return SimpleNamespace(execute=AsyncMock(return_value=[{"cnt": n}]))


@pytest.mark.asyncio
async def test_active_true_when_md_present_and_under_threshold(monkeypatch, tmp_path):
    (tmp_path / "Bootstrap.md").write_text("bootstrap")
    monkeypatch.setattr(greeting_seed, "resolve_existing_workspace", lambda *a, **k: tmp_path)

    active = await greeting_seed._bootstrap_active(
        _db_with_count(2), "a1", "u1", {}  # default threshold 3, count 2 < 3
    )
    assert active is True


@pytest.mark.asyncio
async def test_active_false_when_md_absent(monkeypatch, tmp_path):
    # no Bootstrap.md written
    monkeypatch.setattr(greeting_seed, "resolve_existing_workspace", lambda *a, **k: tmp_path)

    active = await greeting_seed._bootstrap_active(_db_with_count(0), "a1", "u1", {})
    assert active is False


@pytest.mark.asyncio
async def test_active_false_when_over_threshold(monkeypatch, tmp_path):
    (tmp_path / "Bootstrap.md").write_text("bootstrap")
    monkeypatch.setattr(greeting_seed, "resolve_existing_workspace", lambda *a, **k: tmp_path)

    active = await greeting_seed._bootstrap_active(
        _db_with_count(3), "a1", "u1", {}  # count 3 >= threshold 3
    )
    assert active is False
