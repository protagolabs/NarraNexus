"""
@file_name: test_cost_tracker_event_attribution.py
@author: Bin Liang
@date: 2026-08-28
@description: Ambient event attribution for cost rows.

Only step_4 (call_type="agent_loop") ever passed a real event_id; every
helper call site hard-coded event_id=None, so a per-turn token figure
counted the main loop and silently dropped narrative selection, the
shutter/decider, summarisation and post-turn hooks. The fix is an ambient
ContextVar that record_cost falls back to, mirroring how (agent_id, db)
already travel — these tests pin the fallback, the explicit-wins rule, the
scope restore, and the task-copy propagation that background Steps 5-6
depend on.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.utils.cost_tracker import (
    clear_cost_context,
    cost_context_scope,
    cost_event_scope,
    get_cost_context,
    get_cost_event_id,
    record_cost,
)


def _mk_mock_db():
    m = MagicMock()
    m.insert = AsyncMock(return_value=1)
    return m


def _inserted_row(db) -> dict:
    assert db.insert.await_count == 1
    table, row = db.insert.await_args.args
    assert table == "cost_records"
    return row


@pytest.fixture(autouse=True)
def _isolate_context():
    """Each test starts and ends with a clean ambient context."""
    clear_cost_context()
    yield
    clear_cost_context()


# ---------------------------------------------------------------------------
# record_cost fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_helper_call_inherits_ambient_event_id():
    """A helper passing event_id=None lands on the turn that is running."""
    db = _mk_mock_db()
    with cost_event_scope("evt_a1b2c3d4"):
        await record_cost(
            db=db, agent_id="agent_x", event_id=None,
            call_type="llm_function", model="gpt-4o",
            input_tokens=10, output_tokens=5,
        )
    assert _inserted_row(db)["event_id"] == "evt_a1b2c3d4"


@pytest.mark.asyncio
async def test_explicit_event_id_wins_over_ambient():
    """step_4 passes ctx.event.id explicitly; it must not be second-guessed."""
    db = _mk_mock_db()
    with cost_event_scope("evt_ambient"):
        await record_cost(
            db=db, agent_id="agent_x", event_id="evt_explicit",
            call_type="agent_loop", model="claude-opus-5",
            input_tokens=10, output_tokens=5,
        )
    assert _inserted_row(db)["event_id"] == "evt_explicit"


@pytest.mark.asyncio
async def test_no_ambient_event_stays_null():
    """Outside a turn (background worker, script) the column stays NULL."""
    db = _mk_mock_db()
    await record_cost(
        db=db, agent_id="agent_x", event_id=None,
        call_type="llm_function", model="gpt-4o",
        input_tokens=10, output_tokens=5,
    )
    assert _inserted_row(db)["event_id"] is None


# ---------------------------------------------------------------------------
# scope lifecycle
# ---------------------------------------------------------------------------

def test_scope_restores_previous_value_on_exit():
    assert get_cost_event_id() is None
    with cost_event_scope("evt_outer"):
        assert get_cost_event_id() == "evt_outer"
        with cost_event_scope("evt_inner"):
            assert get_cost_event_id() == "evt_inner"
        assert get_cost_event_id() == "evt_outer"
    assert get_cost_event_id() is None


def test_scope_restores_even_when_body_raises():
    with pytest.raises(RuntimeError):
        with cost_event_scope("evt_boom"):
            raise RuntimeError("turn failed")
    assert get_cost_event_id() is None


def test_clear_cost_context_also_clears_the_event():
    """The outermost clear must not leave a turn id behind for the next caller
    in this context — a stale id would misattribute later spend."""
    with cost_event_scope("evt_stale"):
        clear_cost_context()
        assert get_cost_event_id() is None


# ---------------------------------------------------------------------------
# cost_context_scope — the (agent_id, db) half
# ---------------------------------------------------------------------------

def test_context_scope_restores_the_outer_pair_instead_of_clearing_it():
    """The bug this scope exists for.

    step_3's fallback helper used to `set_cost_context(...)` then
    `clear_cost_context()` in a finally. That runs inside an ASYNC GENERATOR,
    which does not get its own context copy — so the clear wiped the turn's
    own (agent_id, db). Everything after it in the turn recorded nothing, and
    Steps 5-6 (spawned later, copying that emptied context) booked no cost at
    all. Restoring instead of clearing is the fix.
    """
    db_outer, db_inner = object(), object()
    with cost_context_scope("agent_outer", db_outer):
        with cost_context_scope("agent_inner", db_inner):
            assert get_cost_context() == ("agent_inner", db_inner)
        assert get_cost_context() == ("agent_outer", db_outer)
    assert get_cost_context() is None


def test_context_scope_restores_when_the_body_raises():
    db = object()
    with cost_context_scope("agent_outer", db):
        with pytest.raises(RuntimeError):
            with cost_context_scope("agent_inner", object()):
                raise RuntimeError("helper blew up")
        assert get_cost_context() == ("agent_outer", db)


def test_context_scope_leaves_the_event_alone():
    """A mid-turn context re-set must not knock the turn's helper spend off
    the turn — the two vars have different lifetimes on purpose."""
    with cost_event_scope("evt_turn"):
        with cost_context_scope("agent_x", object()):
            assert get_cost_event_id() == "evt_turn"
        assert get_cost_event_id() == "evt_turn"


def test_scope_exit_survives_a_token_from_another_context():
    """An async generator abandoned mid-iteration runs its finally under
    whatever context closes it, and `ContextVar.reset` raises
    `Token was created in a different Context` there.

    What is guaranteed: the unwind does not raise into that unrelated
    caller's stack, and the var reads None in the context doing the cleanup.
    What is NOT guaranteed (and cannot be — contextvars has no cross-context
    write): the entering context's value. It keeps the id until its own
    scope unwinds normally. That is why the scope, not the abandonment path,
    is what AgentRuntime relies on.
    """
    import contextvars

    cm = cost_event_scope("evt_cross")
    cm.__enter__()
    seen = {}

    def _exit_elsewhere():
        # Fresh context: the token was created in the caller's, not this one.
        cm.__exit__(None, None, None)  # must not raise ValueError
        seen["after"] = get_cost_event_id()

    contextvars.copy_context().run(_exit_elsewhere)
    assert seen["after"] is None


# ---------------------------------------------------------------------------
# propagation into spawned tasks (background Steps 5-6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawned_task_inherits_the_turns_event_id():
    """Steps 5-6 run in a task spawned from inside the scope: asyncio copies
    the context at create_task time, so post-turn hook spend stays on the turn."""
    seen = {}

    async def _background():
        seen["event_id"] = get_cost_event_id()

    with cost_event_scope("evt_bg"):
        task = asyncio.create_task(_background())
    await task

    assert seen["event_id"] == "evt_bg"
