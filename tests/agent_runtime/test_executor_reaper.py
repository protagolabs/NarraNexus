"""
@file_name: test_executor_reaper.py
@date: 2026-06-17
@description: Idle-cull reaper — stops idle executors, skips failures,
no-op without a broker, and never culls a user whose run is live in another
process. Pure coordinator, tested via DI fakes.
"""
from __future__ import annotations

import asyncio

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

    async def fake_audit(event_type, **kw):
        rows.append((kw.get("user_id"), kw.get("run_id")))

    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.executor_reaper._audit", fake_audit
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

    controller = _FakeController(["a", "b", "c"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=None, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "c"]   # b failed → skipped, pass not aborted
    # Claimed but not stopped ⇒ the stamp goes back, or b's container is
    # never reclaimed. stop_fn is an HTTP call to the broker, so this path
    # runs on every deploy restart and every 5xx.
    assert controller.restamped == ["b"]


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


@pytest.mark.asyncio
async def test_a_failing_restamp_does_not_abort_the_pass():
    """The handler's contract is that one user's failure never aborts the
    pass; that must not depend on what a collaborator does later."""
    class _BrokenRestamp(_FakeController):
        async def restamp_idle(self, user_id):
            raise RuntimeError("controller wedged")

    stopped = []

    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")
        stopped.append(user_id)

    reaper = ExecutorReaper(
        _BrokenRestamp(["a", "b", "c"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    assert await reaper.reap_once() == ["a", "c"]
    assert stopped == ["a", "c"]


# --------------------------------------------------------------------------
# Blind-pass reporting — "culling stopped" vs "nothing to cull"
# --------------------------------------------------------------------------


async def _noop_stop(user_id):
    return None


@pytest.fixture(autouse=True)
def _reset_reaper_module_state():
    import xyz_agent_context.agent_runtime.executor_reaper as mod

    def _clear():
        mod._LAST_PASS = None
        mod._LAST_PASS_AT = None
        mod._TASK_ERROR = None
        mod._recording_off_warned.clear()

    _clear()
    yield
    _clear()


@pytest.fixture
def audit_rows(monkeypatch):
    rows = []

    async def fake_audit(event_type, **kw):
        rows.append((event_type, kw.get("detail")))

    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.executor_reaper._audit", fake_audit
    )
    return rows


def test_status_reports_not_running_before_any_pass():
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    assert reaper_status() == {
        "running": False, "stale": None, "task_error": None,
    }


@pytest.mark.asyncio
async def test_a_wholly_blind_pass_is_counted_and_audited(audit_rows):
    """The failure this exists for: every candidate unreadable ⇒ nothing is
    ever culled, and from outside it looks exactly like an idle system."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def blind(user_id):
        return UNKNOWN_RUN

    reaper = ExecutorReaper(
        _FakeController(["a", "b"]), _noop_stop,
        is_busy=_CullVeto(check=blind), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []

    status = reaper_status()
    assert status["running"] is True
    assert status["blind_passes"] == 1
    assert status["judged"] == 2 and status["blind"] == 2
    assert [r[0] for r in audit_rows] == ["cull_disabled"]
    assert audit_rows[0][1]["judged"] == 2


@pytest.mark.asyncio
async def test_a_real_veto_is_not_a_blind_pass(audit_rows):
    """A live run blocking the cull is the guard WORKING; it must not read as
    the reaper having gone blind."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def busy(user_id):
        return "evt_real"

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=busy), ttl_seconds=1,
    )
    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 0
    assert [r[0] for r in audit_rows] == ["cull_skipped_busy"]


@pytest.mark.asyncio
async def test_blind_passes_reset_once_liveness_is_readable_again(audit_rows):
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    # reap_once asks twice per surviving user (claim, then again before the
    # stop), so this is keyed on the pass, not on a call counter.
    blind = {"still": True}

    async def check(user_id):
        return UNKNOWN_RUN if blind["still"] else None

    controller = _FakeController(["a"])
    reaper = ExecutorReaper(
        controller, _noop_stop, is_busy=_CullVeto(check=check), ttl_seconds=1
    )

    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 1
    blind["still"] = False
    controller._idle = ["a"]
    assert await reaper.reap_once() == ["a"]
    assert reaper_status()["blind_passes"] == 0


@pytest.mark.asyncio
async def test_an_empty_pass_is_not_blind(audit_rows):
    """Nothing due is the healthy steady state — it must not raise the alarm
    the blind counter exists to raise."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def check(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController([]), _noop_stop,
        is_busy=_CullVeto(check=check), ttl_seconds=1,
    )
    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 0
    assert audit_rows == []


@pytest.mark.asyncio
async def test_a_slow_liveness_lookup_counts_as_blind(audit_rows, monkeypatch):
    """The common DB degradation is SLOW, not dead. Bounding the lookup only
    at the admission layer cancels the veto mid-await, so nothing is tallied
    and the pass reports the same zeros as a healthy empty one — the exact
    blind spot the blind-pass alarm exists to close."""
    import xyz_agent_context.agent_runtime.executor_reaper as mod
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    monkeypatch.setattr(mod, "_PER_CANDIDATE_S", 0.02)

    async def wedged(user_id):
        await asyncio.sleep(5)          # never returns within the budget
        return None

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=wedged), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []

    status = reaper_status()
    assert status["judged"] == 1 and status["blind"] == 1
    assert status["blind_passes"] == 1
    assert [r[0] for r in audit_rows] == ["cull_disabled"]


@pytest.mark.asyncio
async def test_a_pass_with_no_candidates_does_not_reset_blind_passes(audit_rows):
    """The kill switch stays on for hours; an idle minute in the middle must
    not punch the counter back to zero and defeat a threshold alert."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def blind(user_id):
        return UNKNOWN_RUN

    veto = _CullVeto(check=blind)
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(controller, _noop_stop, is_busy=veto, ttl_seconds=1)

    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 1
    controller._idle = []                    # nobody due this pass
    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 1   # unchanged, not reset


@pytest.mark.asyncio
async def test_claim_and_recheck_are_counted_separately(audit_rows):
    """One healthy pass asks twice per survivor. Merged, `judged: 10,
    reaped: 5` sends the reader hunting for 5 users that do not exist."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def idle(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController(["a", "b"]), _noop_stop,
        is_busy=_CullVeto(check=idle), ttl_seconds=1,
    )
    assert await reaper.reap_once() == ["a", "b"]

    status = reaper_status()
    assert status["judged"] == 2            # candidates, not questions
    assert status["recheck_judged"] == 2
    assert status["reaped"] == 2


@pytest.mark.asyncio
async def test_a_blind_recheck_is_not_a_blind_pass(audit_rows):
    """Claim phase read the DB fine; a hiccup during the recheck is a hiccup,
    not a reaper that has gone blind."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    seen = {"n": 0}

    async def check(user_id):
        seen["n"] += 1
        return None if seen["n"] == 1 else UNKNOWN_RUN

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=check), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []          # recheck withheld the stop
    status = reaper_status()
    assert status["blind_passes"] == 0
    assert status["recheck_vetoed"] == 1
    assert [r[0] for r in audit_rows] == []        # no cull_disabled


@pytest.mark.asyncio
async def test_a_reaper_without_the_veto_is_reported_not_hidden(audit_rows):
    """is_busy=None is the pre-2026-07-31 configuration: it culls on this
    process's local view alone. Reporting it as "not running" would send the
    reader looking for a leak while runs are being cut off."""
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop, is_busy=None, ttl_seconds=1
    )
    await reaper.reap_once()

    status = reaper_status()
    assert status["running"] is True
    assert status["veto_installed"] is False
    # Explicit zeros, not absent keys — a watcher reading body["judged"]
    # should get a number, not a KeyError.
    for key in ("judged", "vetoed", "blind", "recheck_judged", "recheck_vetoed"):
        assert status[key] == 0


@pytest.mark.asyncio
async def test_status_goes_stale_when_no_pass_completes(audit_rows):
    """"The task exists" is L1 and proves nothing (incident lesson #4): a
    wedged reaper keeps reporting its last good pass forever."""
    import xyz_agent_context.agent_runtime.executor_reaper as mod
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def idle(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController([]), _noop_stop,
        is_busy=_CullVeto(check=idle), ttl_seconds=1, interval_seconds=10,
    )
    await reaper.reap_once()
    assert reaper_status()["stale"] is False

    mod._LAST_PASS_AT -= 31        # > 3 intervals with no completed pass
    status = reaper_status()
    assert status["stale"] is True
    assert status["age_seconds"] >= 31


def test_a_dead_background_task_is_visible_in_status():
    """Its only other trace is one log line the next rotation eats."""
    import xyz_agent_context.agent_runtime.executor_reaper as mod
    from xyz_agent_context.agent_runtime.executor_reaper import reaper_status

    async def _boom():
        raise RuntimeError("reap loop exploded")

    async def _run():
        task = asyncio.get_running_loop().create_task(_boom())
        task.add_done_callback(mod._on_reaper_done)
        with pytest.raises(RuntimeError):
            await task

    asyncio.run(_run())
    assert "reap loop exploded" in (reaper_status()["task_error"] or "")


@pytest.mark.asyncio
async def test_cull_disabled_rows_are_rate_limited_like_the_warning(
    audit_rows, monkeypatch
):
    """One row per pass would make the row count a function of outage
    duration — the shape this file already avoids for run duration."""
    import xyz_agent_context.agent_runtime.executor_reaper as mod

    monkeypatch.setattr(mod, "_BLIND_WARN_EVERY", 3)

    async def blind(user_id):
        return UNKNOWN_RUN

    veto = _CullVeto(check=blind)
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(controller, _noop_stop, is_busy=veto, ttl_seconds=1)

    for _ in range(6):
        controller._idle = ["a"]
        await reaper.reap_once()

    # Passes 1 and 4 write; 2/3/5/6 ride the same tick as the warning.
    assert [r[0] for r in audit_rows] == ["cull_disabled", "cull_disabled"]
    # The cause travels in the row so the reader does not have to guess.
    assert audit_rows[0][1]["recording_disabled"] is False


@pytest.mark.asyncio
async def test_cull_disabled_names_the_kill_switch_when_that_is_the_cause(
    audit_rows, monkeypatch
):
    from xyz_agent_context.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(
        controller, _noop_stop, is_busy=_CullVeto(), ttl_seconds=1
    )
    await reaper.reap_once()
    assert audit_rows[0][1]["recording_disabled"] is True
