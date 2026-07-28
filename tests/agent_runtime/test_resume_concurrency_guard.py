"""
@file_name: test_resume_concurrency_guard.py
@author:
@date: 2026-07-28
@description: The in-process concurrent-resume guard in step_3 (review FIX 1).

Two runs of the SAME agent can overlap (the user chats while a JobModule
trigger fires for the same agent+owner). Without a guard both would validate
the same `agent_cli_sessions` handle and both spawn a CLI with
`--resume <same id>` => two writers on one session JSONL. That failure does not
match the stale-handle ("No conversation found") predicate, so it would surface
as a hard error.

Contract pinned here:
  * at most ONE run at a time holds a handle key
    (agent_id, platform_session_id, framework);
  * the loser COLD-STARTS immediately (`COLD reason=handle_in_use`) — it never
    blocks (resume is an optimization, never a dependency);
  * the lease is released on EVERY exit of the driver region: normal
    completion, exception, and `aclose()` of the abandoned async generator;
  * different keys never block each other.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from loguru import logger

from xyz_agent_context.agent_framework.api_config import claude_config
from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext
from xyz_agent_context.agent_runtime.response_processor import ResponseProcessor
from xyz_agent_context.schema import AgentTextDelta, PathExecutionResult
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.workspace_paths import agent_workspace_path

# The steps package re-exports each step FUNCTION under its module name, so
# attribute-style imports hand back the function — resolve the real module.
step3 = importlib.import_module(
    "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop"
)

AGENT = "agent_guard_test"
USER = "user_guard_test"
SESS = "sess_guard_1"
NARRATIVE = "nar_1"
HANDLE = "cli_session_stored"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeDb:
    """Just enough AsyncDatabaseClient surface for CliSessionRepository.get."""

    def __init__(self, row: dict | None):
        self.row = row

    async def get_one(self, table, filters):
        return self.row


def _row(*, fingerprint: str, working_path: str, **overrides) -> dict:
    base = dict(
        agent_id=AGENT,
        platform_session_id=SESS,
        framework="claude_code",
        cli_session_id=HANDLE,
        config_fingerprint=fingerprint,
        working_path=working_path,
        narrative_id=NARRATIVE,
    )
    base.update(overrides)
    return base


def _session(session_id: str = SESS, narrative_id: str = NARRATIVE) -> SimpleNamespace:
    return SimpleNamespace(session_id=session_id, current_narrative_id=narrative_id)


def _text_delta_event(text: str) -> dict:
    return {
        "type": "raw_response_event",
        "data": {"type": "response.text.delta", "delta": text},
    }


class _FakeContextRuntime:
    """Stands in for the context build (step 3.1/3.2) — no DB, no prompts."""

    def __init__(self, agent_id, user_id, db_client):
        pass

    async def run(self, *args, **kwargs):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "hi"}],
            mcp_servers={},
            disallowed_tools=[],
            ctx_data=SimpleNamespace(extra_data={}),
        )


class _FakeDriver:
    """Yields the given raw events, then optionally raises."""

    def __init__(self, events: list[dict], raise_exc: Exception | None = None):
        self.events = events
        self.raise_exc = raise_exc
        self.kwargs: dict = {}

    async def agent_loop(self, **kwargs):
        self.kwargs = kwargs
        for event in self.events:
            yield event
        if self.raise_exc is not None:
            raise self.raise_exc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_guard():
    """No cross-test leakage of leased keys (module-level, per-process state)."""
    step3._resume_handles_in_use.clear()
    yield
    step3._resume_handles_in_use.clear()


@pytest.fixture
def fingerprint() -> str:
    # The real fingerprint of the ambient claude_config — step_3 recomputes it,
    # so the seeded handle row must carry the same value to pass validation.
    return claude_config.resume_fingerprint()


@pytest.fixture
def working_path(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    return str(agent_workspace_path(AGENT, USER, base=str(tmp_path)))


@pytest.fixture
def loguru_lines():
    """Collect log lines so the greppable COLD reason can be asserted."""
    lines: list[str] = []
    sink_id = logger.add(lambda msg: lines.append(msg), level="INFO")
    yield lines
    logger.remove(sink_id)


# ---------------------------------------------------------------------------
# Lease + validation wrapper (`_acquire_resume_session`)
# ---------------------------------------------------------------------------


async def _acquire(db, *, agent=AGENT, session=None, framework="claude_code",
                   fingerprint="fp", working_path="/wp"):
    return await step3._acquire_resume_session(
        agent_id=agent,
        session=session or _session(),
        framework=framework,
        config_fingerprint=fingerprint,
        working_path=working_path,
        db_client=db,
    )


@pytest.mark.asyncio
async def test_first_run_leases_the_handle():
    db = _FakeDb(_row(fingerprint="fp", working_path="/wp"))
    session_id, key = await _acquire(db)
    assert session_id == HANDLE
    assert key == (AGENT, SESS, "claude_code")
    assert key in step3._resume_handles_in_use


@pytest.mark.asyncio
async def test_second_overlapping_run_cold_starts_with_handle_in_use(loguru_lines):
    db = _FakeDb(_row(fingerprint="fp", working_path="/wp"))
    first_id, first_key = await _acquire(db)
    assert first_id == HANDLE

    # The first run has NOT released yet — the second must fall back to a cold
    # start rather than wait (waiting would stall a turn).
    second_id, second_key = await _acquire(db)
    assert second_id is None
    assert second_key is None
    assert any("COLD reason=handle_in_use" in line for line in loguru_lines)
    # Only the first holder's key is leased, exactly once.
    assert step3._resume_handles_in_use == {first_key}


@pytest.mark.asyncio
async def test_handle_is_resumable_again_after_release():
    db = _FakeDb(_row(fingerprint="fp", working_path="/wp"))
    _, key = await _acquire(db)
    step3._release_resume_handle(key)
    assert step3._resume_handles_in_use == set()

    again_id, again_key = await _acquire(db)
    assert again_id == HANDLE
    assert again_key == key


@pytest.mark.asyncio
async def test_release_is_idempotent_and_never_raises():
    key = (AGENT, SESS, "claude_code")
    step3._release_resume_handle(key)  # never leased
    assert step3._try_acquire_resume_handle(key) is True
    step3._release_resume_handle(key)
    step3._release_resume_handle(key)
    assert step3._resume_handles_in_use == set()


@pytest.mark.asyncio
async def test_different_agent_never_blocks():
    db = _FakeDb(_row(fingerprint="fp", working_path="/wp"))
    await _acquire(db)
    other_id, other_key = await _acquire(db, agent="agent_other")
    assert other_id == HANDLE
    assert other_key == ("agent_other", SESS, "claude_code")


@pytest.mark.asyncio
async def test_different_platform_session_never_blocks():
    db = _FakeDb(_row(fingerprint="fp", working_path="/wp"))
    await _acquire(db)
    other_id, other_key = await _acquire(db, session=_session(session_id="sess_other"))
    assert other_id == HANDLE
    assert other_key == (AGENT, "sess_other", "claude_code")


@pytest.mark.asyncio
async def test_different_framework_never_blocks():
    db = _FakeDb(_row(fingerprint="fp", working_path="/wp", framework="codex_cli"))
    await _acquire(db)
    other_id, other_key = await _acquire(db, framework="codex_cli")
    assert other_id == HANDLE
    assert other_key == (AGENT, SESS, "codex_cli")


@pytest.mark.asyncio
async def test_no_lease_taken_when_validation_says_cold():
    # A run that would not have resumed anyway must not lease the key — else it
    # would block the run that WOULD resume.
    db = _FakeDb(_row(fingerprint="other_fp", working_path="/wp"))
    session_id, key = await _acquire(db)
    assert (session_id, key) == (None, None)
    assert step3._resume_handles_in_use == set()


# ---------------------------------------------------------------------------
# step_3 end-to-end: the lease is held across the driver run and always freed
# ---------------------------------------------------------------------------


def _patch_step3(monkeypatch, driver: _FakeDriver) -> None:
    async def _framework(agent_id, db_client):
        return "claude_code"

    monkeypatch.setattr(step3, "ContextRuntime", _FakeContextRuntime)
    monkeypatch.setattr(step3, "_resolve_agent_framework_name", _framework)
    monkeypatch.setattr(step3, "get_agent_loop_driver", lambda **kwargs: driver)


def _ctx(session_id: str = SESS) -> RunContext:
    return RunContext(
        agent_id=AGENT,
        user_id=USER,
        input_content="hello",
        # Non-chat source: keeps the helper_llm recovery path out of the way,
        # which is orthogonal to the guard under test.
        working_source="job",
        session=_session(session_id=session_id),
    )


def _step3(ctx: RunContext, db) -> object:
    return step3.step_3_agent_loop(ctx, db, ResponseProcessor())


KEY = (AGENT, SESS, "claude_code")


async def _consume_until_first_delta(agen) -> None:
    """Drive the step until it is suspended at a yield INSIDE the driver region
    (i.e. while the lease is held)."""
    while True:
        msg = await agen.__anext__()
        if isinstance(msg, AgentTextDelta):
            return


@pytest.mark.asyncio
async def test_lease_held_during_loop_and_released_on_completion(
    monkeypatch, fingerprint, working_path
):
    driver = _FakeDriver([_text_delta_event("a"), _text_delta_event("b")])
    _patch_step3(monkeypatch, driver)
    db = _FakeDb(_row(fingerprint=fingerprint, working_path=working_path))

    agen = _step3(_ctx(), db)
    await _consume_until_first_delta(agen)
    # Mid-run: this run HOLDS the handle and actually asked to resume it.
    assert KEY in step3._resume_handles_in_use
    assert driver.kwargs.get("resume_session_id") == HANDLE

    tail = [msg async for msg in agen]
    assert any(isinstance(m, PathExecutionResult) for m in tail)
    assert step3._resume_handles_in_use == set()


@pytest.mark.asyncio
async def test_lease_released_when_the_loop_raises(monkeypatch, fingerprint, working_path):
    driver = _FakeDriver(
        [_text_delta_event("a")], raise_exc=RuntimeError("loop blew up")
    )
    _patch_step3(monkeypatch, driver)
    db = _FakeDb(_row(fingerprint=fingerprint, working_path=working_path))

    messages = [msg async for msg in _step3(_ctx(), db)]
    # The turn still finishes with a PathExecutionResult (the error is surfaced
    # through the recovery slot, not by escaping) — and the lease is gone.
    assert any(isinstance(m, PathExecutionResult) for m in messages)
    assert step3._resume_handles_in_use == set()


@pytest.mark.asyncio
async def test_lease_released_on_generator_close_midstream(
    monkeypatch, fingerprint, working_path
):
    """The consumer walks away mid-turn (WS drop / cancelled run): closing the
    async generator throws GeneratorExit at the yield inside the driver region.
    The `finally` must still run — it is synchronous precisely so it can."""
    driver = _FakeDriver([_text_delta_event("a"), _text_delta_event("b")])
    _patch_step3(monkeypatch, driver)
    db = _FakeDb(_row(fingerprint=fingerprint, working_path=working_path))

    agen = _step3(_ctx(), db)
    await _consume_until_first_delta(agen)
    assert KEY in step3._resume_handles_in_use

    await agen.aclose()
    assert step3._resume_handles_in_use == set()


@pytest.mark.asyncio
async def test_overlapping_runs_second_turn_cold_starts(
    monkeypatch, fingerprint, working_path
):
    """The real hazard, end to end: run B starts while run A is mid-loop on the
    same (agent, platform_session, framework). B must NOT be handed the same
    --resume id."""
    row = _row(fingerprint=fingerprint, working_path=working_path)
    driver_a = _FakeDriver([_text_delta_event("a"), _text_delta_event("a2")])
    driver_b = _FakeDriver([_text_delta_event("b")])

    _patch_step3(monkeypatch, driver_a)
    agen_a = _step3(_ctx(), _FakeDb(row))
    await _consume_until_first_delta(agen_a)
    assert driver_a.kwargs.get("resume_session_id") == HANDLE

    # B runs entirely while A still holds the lease.
    monkeypatch.setattr(step3, "get_agent_loop_driver", lambda **kwargs: driver_b)
    _ = [msg async for msg in _step3(_ctx(), _FakeDb(row))]
    assert "resume_session_id" not in driver_b.kwargs  # cold start

    # A finishes normally and frees the handle for the next turn.
    _ = [msg async for msg in agen_a]
    assert step3._resume_handles_in_use == set()
