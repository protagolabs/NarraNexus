"""
@file_name: test_greeting_seed.py
@author: Bin Liang
@date: 2026-08-20
@description: Lock seed_first_greeting_message — the bootstrap greeting must be
persisted as a new chat instance's FIRST assistant message, but ONLY while the
agent is still bootstrapping (Bootstrap.md present) and carries a greeting.

Deleting the write, the greeting guard, the Bootstrap.md guard, or the
create-time timestamp anchor each turns one of these tests red.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from xyz_agent_context.bootstrap import greeting_seed


class _CapturingEventRepo:
    captured: list = []

    def __init__(self, agent_id, user_id, db):
        pass

    async def add_instance_json_format_memory(self, module_name, instance_id, memory):
        _CapturingEventRepo.captured.append((module_name, instance_id, memory))
        return True


def _agent_repo_returning(agent):
    class _Repo:
        def __init__(self, db):
            pass

        async def get_agent(self, agent_id):
            return agent

    return _Repo


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    _CapturingEventRepo.captured = []
    monkeypatch.setattr(greeting_seed, "EventMemoryRepository", _CapturingEventRepo)
    # Bootstrap.md present by default; individual tests override.
    monkeypatch.setattr(greeting_seed.os.path, "isfile", lambda p: True)
    yield


_CREATE_TIME = datetime(2026, 8, 20, 4, 10, 0, tzinfo=timezone.utc)


def _agent(metadata, create_time=_CREATE_TIME):
    return SimpleNamespace(agent_metadata=metadata, agent_create_time=create_time)


@pytest.mark.asyncio
async def test_seeds_greeting_when_bootstrapping(monkeypatch):
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "Hi, I'm Echo!"})),
    )

    wrote = await greeting_seed.seed_first_greeting_message(
        db=object(), agent_id="a1", user_id="u1", instance_id="chat_new"
    )

    assert wrote is True
    assert len(_CapturingEventRepo.captured) == 1
    module_name, instance_id, memory = _CapturingEventRepo.captured[0]
    assert module_name == "ChatModule"
    assert instance_id == "chat_new"
    msgs = memory["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "Hi, I'm Echo!"
    assert msgs[0]["meta_data"]["bootstrap"] is True
    assert msgs[0]["meta_data"]["instance_id"] == "chat_new"


@pytest.mark.asyncio
async def test_timestamp_anchored_to_agent_create_time(monkeypatch):
    """Greeting ts = agent creation time, guaranteeing it sorts before any turn."""
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "hey"})),
    )

    await greeting_seed.seed_first_greeting_message(
        db=object(), agent_id="a1", user_id="u1", instance_id="chat_new"
    )

    _, _, memory = _CapturingEventRepo.captured[0]
    assert memory["messages"][0]["meta_data"]["timestamp"] == _CREATE_TIME.isoformat()
    # A real user message stamped at "now" (agent loop runs long after creation)
    # must sort strictly after the greeting.
    assert _CREATE_TIME < datetime.now(timezone.utc) - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_no_seed_when_no_greeting(monkeypatch):
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({})),  # no bootstrap_greeting
    )

    wrote = await greeting_seed.seed_first_greeting_message(
        db=object(), agent_id="a1", user_id="u1", instance_id="chat_new"
    )

    assert wrote is False
    assert _CapturingEventRepo.captured == []


@pytest.mark.asyncio
async def test_no_seed_when_bootstrap_inactive(monkeypatch):
    """Greeting present but Bootstrap.md gone (auto-deleted) → never re-greet."""
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "hi"})),
    )
    monkeypatch.setattr(greeting_seed.os.path, "isfile", lambda p: False)

    wrote = await greeting_seed.seed_first_greeting_message(
        db=object(), agent_id="a1", user_id="u1", instance_id="chat_new"
    )

    assert wrote is False
    assert _CapturingEventRepo.captured == []


@pytest.mark.asyncio
async def test_no_seed_when_create_time_missing(monkeypatch):
    """No agent_create_time → defer to ChatModule.hook_persist_turn (which
    stamps event.created_at - 1ms). Seeding with now() would land AFTER the
    user message (this runs mid-turn) — the P0 ordering bug — so we must NOT
    write, we return False."""
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "hi"}, create_time=None)),
    )

    wrote = await greeting_seed.seed_first_greeting_message(
        db=object(), agent_id="a1", user_id="u1", instance_id="chat_new"
    )

    assert wrote is False
    assert _CapturingEventRepo.captured == []


@pytest.mark.asyncio
async def test_no_seed_when_agent_missing(monkeypatch):
    monkeypatch.setattr(
        greeting_seed, "AgentRepository", _agent_repo_returning(None)
    )

    wrote = await greeting_seed.seed_first_greeting_message(
        db=object(), agent_id="a1", user_id="u1", instance_id="chat_new"
    )

    assert wrote is False
    assert _CapturingEventRepo.captured == []
