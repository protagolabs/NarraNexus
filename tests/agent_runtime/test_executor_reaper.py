"""
@file_name: test_executor_reaper.py
@date: 2026-06-17
@description: Idle-cull reaper — stops idle executors, skips failures,
no-op without a broker, and never culls a user whose run is live in another
process. Pure coordinator, tested via DI fakes.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.executor_reaper import (
    UNKNOWN_RUN,
    ExecutorReaper,
    _CullVeto,
    live_run_elsewhere,
    maybe_start_executor_reaper,
)


class _FakeController:
    """Returns a fixed idle set once, then empty (mimics claim semantics).

    Applies the injected veto the way the real controller does — vetoed users
    are withheld AND keep their stamp, so they are re-offered next pass.
    """

    def __init__(self, idle):
        self._idle = list(idle)
        self.restamped: list[str] = []

    async def claim_idle_users(self, ttl_seconds, is_busy=None):
        users, self._idle = self._idle, []
        if is_busy is None:
            return users
        kept, claimed = [], []
        for user_id in users:
            (kept if await is_busy(user_id) else claimed).append(user_id)
        self._idle = kept
        return claimed

    async def restamp_idle(self, user_id):
        self.restamped.append(user_id)


@pytest.fixture
def audited(monkeypatch):
    """Capture cull-skip audit rows instead of reaching for a DB client."""
    rows: list[tuple[str, str]] = []

    async def fake_audit(user_id, run_id):
        rows.append((user_id, run_id))

    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.executor_reaper._audit_cull_skipped",
        fake_audit,
    )
    return rows


@pytest.mark.asyncio
async def test_reap_once_stops_all_idle_users():
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    reaper = ExecutorReaper(
        _FakeController(["a", "b"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    reaped = await reaper.reap_once()
    assert reaped == ["a", "b"]
    assert stopped == ["a", "b"]


@pytest.mark.asyncio
async def test_reap_once_skips_stop_failures():
    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")

    reaper = ExecutorReaper(
        _FakeController(["a", "b", "c"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    reaped = await reaper.reap_once()
    assert reaped == ["a", "c"]   # b failed → skipped, pass not aborted


@pytest.mark.asyncio
async def test_reap_once_empty_when_nothing_idle():
    reaper = ExecutorReaper(
        _FakeController([]), lambda u: None, is_busy=None, ttl_seconds=1
    )
    assert await reaper.reap_once() == []


@pytest.mark.asyncio
async def test_a_stop_failure_does_not_abort_the_pass():
    """One user's broker hiccup must not stop the others from being culled —
    the broker's own label-based reaper is the backstop for the one that
    failed."""
    stopped = []

    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")
        stopped.append(user_id)

    reaper = ExecutorReaper(
        _FakeController(["a", "b", "c"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    reaped = await reaper.reap_once()

    assert stopped == ["a", "c"]
    assert reaped == ["a", "c"]


def test_maybe_start_is_noop_without_broker(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert maybe_start_executor_reaper() is None


# --------------------------------------------------------------------------
# Cross-process liveness — the 2026-07-31 incident
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_busy_in_another_process_is_not_reaped():
    """The incident itself: idle here (web chat ended 20min ago), live in
    workers (group-chat reply in flight)."""
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    async def is_busy(user_id):
        return user_id == "busy-elsewhere"

    controller = _FakeController(["idle", "busy-elsewhere"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=1)

    assert await reaper.reap_once() == ["idle"]
    assert stopped == ["idle"]


@pytest.mark.asyncio
async def test_a_vetoed_user_keeps_its_stamp_and_is_reoffered():
    """Withholding must not double as forgetting — otherwise the fix trades
    the kill for a container that never gets reaped at all."""
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    busy = {"u"}

    async def is_busy(user_id):
        return user_id in busy

    controller = _FakeController(["u"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=1)

    assert await reaper.reap_once() == []      # run still live
    busy.clear()                                # run finished
    assert await reaper.reap_once() == ["u"]    # re-offered, now culled
    assert stopped == ["u"]


@pytest.mark.asyncio
async def test_user_that_became_busy_between_claim_and_stop_is_restamped():
    """Stops are sequential and `docker stop` waits out a SIGTERM grace, so a
    verdict taken at claim time can be minutes stale by the time this user's
    turn comes."""
    stopped = []
    calls = {"n": 0}

    async def stop_fn(user_id):
        stopped.append(user_id)

    async def is_busy(user_id):
        calls["n"] += 1
        return calls["n"] > 1   # idle at claim time, busy at stop time

    controller = _FakeController(["u"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=1)

    assert await reaper.reap_once() == []
    assert stopped == []
    assert controller.restamped == ["u"]   # not dropped from idle tracking


@pytest.mark.asyncio
async def test_veto_dedupes_audit_per_run_not_per_pass(audited):
    """A 10-hour agent (rule #14: normal) blocks ~300 passes. The metric
    counts runs saved, so it must write one row, not 300."""
    async def check(user_id):
        return "evt_long"

    veto = _CullVeto(check=check)
    for _ in range(5):
        assert await veto("u") is True

    assert audited == [("u", "evt_long")]
    assert veto._blocked_by == {"u": "evt_long"}


@pytest.mark.asyncio
async def test_veto_re_audits_when_a_different_run_blocks(audited):
    seen = ["evt_one", "evt_one", "evt_two"]

    async def check(user_id):
        return seen.pop(0)

    veto = _CullVeto(check=check)
    await veto("u")
    await veto("u")
    await veto("u")
    assert veto._blocked_by == {"u": "evt_two"}


@pytest.mark.asyncio
async def test_veto_forgets_a_user_that_came_back_clean(audited):
    answers = ["evt_x", None]

    async def check(user_id):
        return answers.pop(0)

    veto = _CullVeto(check=check)
    assert await veto("u") is True
    assert await veto("u") is False
    assert veto._blocked_by == {}


@pytest.mark.asyncio
async def test_veto_tracking_is_bounded(audited):
    async def check(user_id):
        return f"evt_{user_id}"

    veto = _CullVeto(check=check)
    for i in range(_CullVeto._MAX_TRACKED + 50):
        await veto(f"u{i}")
    assert len(veto._blocked_by) == _CullVeto._MAX_TRACKED


@pytest.mark.asyncio
async def test_unreadable_db_reads_as_busy(monkeypatch):
    """Not knowing must never authorise a stop (rule #14)."""
    async def boom():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", boom
    )
    assert await live_run_elsewhere("u") == UNKNOWN_RUN


@pytest.mark.asyncio
async def test_recording_kill_switch_disables_culling(monkeypatch):
    """The switch turns off the very rows this guard reads, so it must not
    silently become a licence to destroy containers."""
    from xyz_agent_context.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    assert await live_run_elsewhere("u") == UNKNOWN_RUN


@pytest.mark.asyncio
async def test_unknown_verdicts_are_not_audited(audited):
    """Rows must count runs actually saved; 'we could not tell' is a log and
    an alert, not a near-miss."""
    async def unknown(user_id):
        return UNKNOWN_RUN

    assert await _CullVeto(check=unknown)("u") is True
    assert audited == []

    async def real(user_id):
        return "evt_real"

    assert await _CullVeto(check=real)("u") is True
    assert audited == [("u", "evt_real")]


def test_production_wiring_installs_the_cross_process_veto(monkeypatch):
    """The guard is only real if the production factory installs it. Without
    this, a reaper constructed with is_busy=None would silently restore the
    pre-2026-07-31 behaviour and every other test here would still pass."""
    import asyncio

    import xyz_agent_context.agent_runtime.executor_reaper as mod

    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.loop.broker_client.broker_url",
        lambda: "http://broker:8030",
    )
    built = {}

    class _Spy(mod.ExecutorReaper):
        def __init__(self, *a, **kw):
            built.update(kw)
            super().__init__(*a, **kw)

        async def run_forever(self):
            return None

    monkeypatch.setattr(mod, "ExecutorReaper", _Spy)

    async def _go():
        task = mod.maybe_start_executor_reaper()
        assert task is not None
        await task

    asyncio.run(_go())
    assert isinstance(built.get("is_busy"), mod._CullVeto)
