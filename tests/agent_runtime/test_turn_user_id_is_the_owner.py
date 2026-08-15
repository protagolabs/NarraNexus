"""
@file_name: test_turn_user_id_is_the_owner.py
@author: NarraNexus
@date: 2026-08-15
@description: Whoever triggered the turn, `ctx.user_id` is the agent's OWNER.

`run()` takes a `user_id` argument that every trigger fills with whatever it
has: the web chat passes the person typing, the bus trigger passes the PEER
AGENT that sent the message (or `team_<id>` for a leader patrol), Lark passes
the Lark sender. Immediately after the agent row is loaded, that argument is
replaced with `agents.created_by` — and everything downstream reads the
replacement.

Why this is load-bearing rather than tidy-up. `ctx.user_id` decides:

* which ChatModule instance the turn binds to (instances are keyed
  `(narrative_id, user_id)`), hence WHICH CONVERSATION HISTORY it loads;
* the agent's workspace directory (`{base}/{user_id}/…`) and the per-user
  shared tree the turn may reach;
* which per-user Executor is provisioned for the turn.

Without the override, a team room would shard all three per speaker: every
teammate that @mentioned the agent would get its own instance with its own
history, its own workspace, its own executor — and the agent would meet each
of them cold, every time. That is exactly the "cold start" a team-room design
pass suspected was still live; it is not, and this file is why.

The invariant had no test at all before this one, in a codebase where three
separate subsystems silently depend on it. It goes through the real `run()`:
RunContext is spied at the attribute `run()` resolves it through, and
construction aborts the moment the kwargs are captured — no steps execute.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.agent_runtime.agent_runtime as agent_runtime_module
from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime

OWNER = "usr_real_owner"
AGENT = "agent_under_test"


class _CtxCaptured(BaseException):
    # BaseException on purpose: run()'s broad `except Exception` handlers must
    # not swallow the sentinel, so pytest.raises stays exact.
    pass


async def _ctx_user_id_for(monkeypatch, db_client, *, triggered_by: str) -> str:
    """Run one turn as `triggered_by` and report the user_id ctx actually got."""
    captured: dict = {}

    class _SpyCtx:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            raise _CtxCaptured()

    monkeypatch.setattr(agent_runtime_module, "RunContext", _SpyCtx)

    runtime = AgentRuntime()

    async def _db(*_a, **_k):
        return db_client

    monkeypatch.setattr(runtime, "_ensure_database_client", _db)

    gen = runtime.run(
        agent_id=AGENT, user_id=triggered_by, input_content="hi"
    )
    with pytest.raises(_CtxCaptured):
        async for _ in gen:
            pass
    assert captured, "RunContext was never constructed"
    return captured["user_id"]


@pytest.fixture
async def seeded(db_client):
    await db_client.insert(
        "agents",
        {"agent_id": AGENT, "agent_name": "A", "created_by": OWNER},
    )
    return db_client


@pytest.mark.asyncio
async def test_a_peer_agents_message_still_runs_as_the_owner(
    monkeypatch, seeded
):
    """The bus trigger passes the SENDING AGENT as user_id."""
    got = await _ctx_user_id_for(monkeypatch, seeded, triggered_by="agent_peer_b")
    assert got == OWNER


@pytest.mark.asyncio
async def test_two_different_speakers_land_on_the_same_user_id(
    monkeypatch, seeded
):
    """The one that matters for a team room: two teammates @mention the same
    agent, and both turns must key on the same value — otherwise instance,
    history, workspace and executor all fork per speaker."""
    first = await _ctx_user_id_for(monkeypatch, seeded, triggered_by="agent_ana")
    second = await _ctx_user_id_for(monkeypatch, seeded, triggered_by="agent_bruno")

    assert first == second == OWNER


@pytest.mark.asyncio
async def test_a_leader_patrol_runs_as_the_owner_too(monkeypatch, seeded):
    """The patrol passes a synthetic `team_<id>` marker rather than an agent —
    a third distinct value for the same room, and it must not fork either."""
    got = await _ctx_user_id_for(monkeypatch, seeded, triggered_by="team_t1")
    assert got == OWNER


@pytest.mark.asyncio
async def test_an_unknown_agent_keeps_what_the_caller_passed(
    monkeypatch, db_client
):
    """No agent row, nothing to resolve — the argument stands rather than being
    replaced with an empty string. Pins the guard, not just the happy path: an
    override that fired on a missing row would blank out the workspace path.
    """
    got = await _ctx_user_id_for(monkeypatch, db_client, triggered_by="usr_x")
    assert got == "usr_x"
