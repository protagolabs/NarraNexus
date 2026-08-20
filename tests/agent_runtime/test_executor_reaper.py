"""
@file_name: test_executor_reaper.py
@date: 2026-06-17
@description: Idle-cull reaper — stops idle executors, skips failures,
no-op without a broker. Pure coordinator, tested via DI fakes.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.executor_reaper import (
    ExecutorReaper,
    maybe_start_executor_reaper,
)


class _FakeController:
    """Returns a fixed idle set once, then empty (mimics claim semantics).

    Applies the reaper's ``is_busy`` veto the way the real controller does,
    so a vetoed user is neither returned nor consumed.
    """

    def __init__(self, idle):
        self._idle = list(idle)

    async def claim_idle_users(self, ttl_seconds, is_busy=None):
        keep, users = [], []
        for u in self._idle:
            if is_busy is not None and await is_busy(u):
                keep.append(u)
            else:
                users.append(u)
        self._idle = keep
        return users


@pytest.mark.asyncio
async def test_reap_once_stops_all_idle_users():
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    reaper = ExecutorReaper(_FakeController(["a", "b"]), stop_fn, is_busy=None, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "b"]
    assert stopped == ["a", "b"]


@pytest.mark.asyncio
async def test_reap_once_skips_stop_failures():
    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")

    reaper = ExecutorReaper(_FakeController(["a", "b", "c"]), stop_fn, is_busy=None, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "c"]   # b failed → skipped, pass not aborted


@pytest.mark.asyncio
async def test_reap_once_empty_when_nothing_idle():
    reaper = ExecutorReaper(_FakeController([]), lambda u: None, is_busy=None, ttl_seconds=1)
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



@pytest.mark.asyncio
async def test_user_busy_in_another_process_is_never_stopped():
    """The 2026-07-31 regression: backend's reaper stopped the container out
    from under a group-chat run that was alive in the workers process."""
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    async def is_busy(user_id):
        return user_id == "group_chat_user"

    reaper = ExecutorReaper(
        _FakeController(["group_chat_user", "truly_idle"]),
        stop_fn,
        is_busy=is_busy,
        ttl_seconds=1,
    )
    assert await reaper.reap_once() == ["truly_idle"]
    assert stopped == ["truly_idle"]


@pytest.mark.asyncio
async def test_cross_process_check_says_busy_when_db_is_unreachable(monkeypatch):
    """No verdict must never authorise a cull (rule #14)."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime.executor_reaper import live_run_elsewhere

    async def _boom():
        raise RuntimeError("no pool")

    monkeypatch.setattr(db_factory, "get_db_client", _boom)
    assert await live_run_elsewhere("u") is not None   # sentinel = "hands off"


@pytest.mark.asyncio
async def test_cross_process_check_reads_live_runs_and_audits_the_skip(
    monkeypatch, db_client,
):
    """End-to-end over a real DB: a live run vetoes the cull and leaves an
    audit row; a finished one does not."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime.executor_reaper import _CullVeto
    from xyz_agent_context.schema.executor_audit import EVENT_CULL_SKIPPED_BUSY
    from xyz_agent_context.utils.timezone import utc_now

    async def _client():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _client)

    base = {
        "trigger": "message_bus",
        "trigger_source": "team_room",
        "agent_id": "agent_x",
        "created_at": "2026-07-31T00:00:00",
        "updated_at": "2026-07-31T00:00:00",
    }
    await db_client.insert("events", {
        **base, "event_id": "evt_live", "user_id": "busy_user",
        "state": "running", "started_at": utc_now(), "last_event_at": utc_now(),
    })
    await db_client.insert("events", {
        **base, "event_id": "evt_done", "user_id": "idle_user",
        "state": "completed", "started_at": utc_now(), "last_event_at": utc_now(),
    })

    veto = _CullVeto()
    with veto.pass_():
        assert await veto("busy_user") is True
        assert await veto("idle_user") is False

    rows = await db_client.get(
        "instance_executor_audit", {"event_type": EVENT_CULL_SKIPPED_BUSY}
    )
    assert [(r["user_id"], r["run_id"]) for r in rows] == [("busy_user", "evt_live")]

    # ...and the SAME live run over the next passes must not keep writing
    # rows, or the metric counts run DURATION instead of runs saved: a legal
    # 10h agent (rule #14) would look like hundreds of near-misses.
    for _ in range(3):
        with veto.pass_():
            assert await veto("busy_user") is True
    rows = await db_client.get(
        "instance_executor_audit", {"event_type": EVENT_CULL_SKIPPED_BUSY}
    )
    assert len(rows) == 1

    # A LATER run of the same user is a new near-miss and must be recorded.
    await db_client.update(
        "events", {"event_id": "evt_live"}, {"state": "completed"},
    )
    await db_client.insert("events", {
        **base, "event_id": "evt_next", "user_id": "busy_user",
        "state": "running", "started_at": utc_now(), "last_event_at": utc_now(),
    })
    with veto.pass_():
        assert await veto("busy_user") is True
    rows = await db_client.get(
        "instance_executor_audit", {"event_type": EVENT_CULL_SKIPPED_BUSY}
    )
    assert [r["run_id"] for r in rows] == ["evt_live", "evt_next"]

    # A user that drops OUT of the candidate set for a while (it went active
    # in THIS process, so acquire() popped its idle stamp) and comes back
    # blocked by the SAME run must not be audited twice: the metric counts
    # runs saved, not how often we happened to look. Mixed chat + group-chat
    # users are exactly the incident's trigger profile.
    for _ in range(3):
        with veto.pass_():
            pass                       # not a candidate at all this pass
    with veto.pass_():
        assert await veto("busy_user") is True
    rows = await db_client.get(
        "instance_executor_audit", {"event_type": EVENT_CULL_SKIPPED_BUSY}
    )
    assert [r["run_id"] for r in rows] == ["evt_live", "evt_next"]


@pytest.mark.asyncio
async def test_recording_kill_switch_stops_all_culling(monkeypatch):
    """The switch that disables trigger-path run recording disables the ONLY
    evidence this guard reads — and it disables it exactly for the runs the
    guard protects. It must not silently degrade into the old behaviour."""
    from xyz_agent_context.agent_runtime.executor_reaper import live_run_elsewhere
    from xyz_agent_context.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    assert await live_run_elsewhere("anyone") is not None   # nothing is reapable


@pytest.mark.asyncio
async def test_verdict_excludes_the_asking_run(monkeypatch, db_client):
    """By step 3 the asking run's own events row is already 'running'. If the
    verdict counted it, allow_replace would be False forever and stale
    executor images would never roll — trading one silent failure for
    another."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime.executor_reaper import (
        stale_replacement_is_safe,
    )
    from xyz_agent_context.utils.timezone import utc_now

    async def _client():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _client)
    await db_client.insert("events", {
        "event_id": "evt_me", "trigger": "chat", "trigger_source": "ws",
        "agent_id": "agent_x", "user_id": "alice", "state": "running",
        "started_at": utc_now(), "last_event_at": utc_now(),
        "created_at": "2026-08-19T00:00:00", "updated_at": "2026-08-19T00:00:00",
    })

    assert await stale_replacement_is_safe("alice", active_run_id="evt_me") is True
    assert await stale_replacement_is_safe("alice") is False   # counts it


@pytest.mark.asyncio
async def test_verdict_defers_when_it_cannot_be_computed(monkeypatch):
    """An unanswerable question must not authorise destroying a container."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime.executor_reaper import (
        stale_replacement_is_safe,
    )

    async def _boom():
        raise RuntimeError("no pool")

    monkeypatch.setattr(db_factory, "get_db_client", _boom)
    assert await stale_replacement_is_safe("alice") is False


def test_maybe_start_is_noop_without_broker(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert maybe_start_executor_reaper() is None


@pytest.mark.asyncio
async def test_recording_switch_freezes_image_rolls_too(monkeypatch):
    """The kill switch has TWO consequences, and the log must name the right
    one for whoever is reading it.

    Idle culling stops (correct — we cannot see in-flight runs), and so does
    stale-image replacement, because the same unknowable answer feeds both.
    Letting replacement through instead would mean destroying containers with
    no view of in-flight runs at all — this whole change in reverse.
    """
    from xyz_agent_context.agent_runtime import executor_reaper as er
    from xyz_agent_context.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    monkeypatch.setattr(er, "_recording_off_warned", set())

    assert await er.live_run_elsewhere("u") == er._UNKNOWN_RECORDING_OFF
    assert await er.stale_replacement_is_safe("u") is False   # image frozen too

    # ...and the warning is emitted once per (caller, user), not once per turn:
    # step 3 calls this on EVERY turn, and the switch is pulled precisely when
    # somebody is reading the logs for something else.
    assert er._recording_off_warned == {("reaper", "u"), ("stale-replace", "u")}


@pytest.mark.asyncio
async def test_unknown_causes_stay_distinguishable(monkeypatch):
    """'We could not tell' is not 'we saved a run'. The two causes need
    different responses, so they must not collapse into one sentinel."""
    import xyz_agent_context.utils.db.db_factory as db_factory
    from xyz_agent_context.agent_runtime import executor_reaper as er

    async def _boom():
        raise RuntimeError("no pool")

    monkeypatch.setattr(db_factory, "get_db_client", _boom)
    assert await er.live_run_elsewhere("u") == er._UNKNOWN_DB_UNAVAILABLE
    assert er._UNKNOWN_DB_UNAVAILABLE != er._UNKNOWN_RECORDING_OFF
    for sentinel in (er._UNKNOWN_DB_UNAVAILABLE, er._UNKNOWN_RECORDING_OFF):
        assert sentinel.startswith("unknown:")   # the filter the metric needs
        assert len(sentinel) <= 128              # events/audit run_id column


@pytest.mark.asyncio
async def test_a_skipped_stop_puts_the_idle_stamp_back():
    """The pre-stop re-check must not reintroduce the leak the whole design
    avoids: claiming is destructive, so a claimed-then-skipped user would
    never be reconsidered until it next goes idle IN THIS PROCESS — which for
    a user driven from workers is never."""
    from xyz_agent_context.agent_runtime.admission import AgentAdmissionController

    now = {"t": 0.0}
    controller = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await controller.release(await controller.acquire("u"))
    now["t"] = 9999.0
    busy = {"v": False}

    async def is_busy(user_id):
        return busy["v"]

    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)

    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=60)

    # Claim says idle; the run starts in the window before the stop lands.
    original_claim = controller.claim_idle_users

    async def claim_then_go_busy(*a, **kw):
        users = await original_claim(*a, **kw)
        busy["v"] = True
        return users

    controller.claim_idle_users = claim_then_go_busy      # type: ignore[assignment]
    assert await reaper.reap_once() == []
    assert stopped == []
    assert "u" in controller._idle_since                  # stamp restored

    # The restored stamp reads "idle as of now" — the claim consumed the
    # original and the user genuinely was busy a moment ago — so it waits one
    # more TTL rather than being reaped immediately. Then it IS reaped: the
    # container is reclaimable again, which is the whole point of restoring.
    assert controller._idle_since["u"] == 9999.0
    controller.claim_idle_users = original_claim          # type: ignore[assignment]
    busy["v"] = False
    assert await reaper.reap_once() == []                 # inside the new TTL
    now["t"] = 20000.0
    assert await reaper.reap_once() == ["u"]


@pytest.mark.asyncio
async def test_restamp_never_pushes_an_existing_stamp_forward():
    """setdefault, not assignment — overwriting would hand the user a free
    extra TTL every time a stop is skipped."""
    from xyz_agent_context.agent_runtime.admission import AgentAdmissionController

    now = {"t": 100.0}
    c = AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    await c.release(await c.acquire("u"))          # idle @ 100
    now["t"] = 500.0
    await c.restamp_idle("u")
    assert c._idle_since["u"] == 100.0

    # An ACTIVE user is not idle at all and must not gain a stamp.
    await c.acquire("v")
    await c.restamp_idle("v")
    assert "v" not in c._idle_since
