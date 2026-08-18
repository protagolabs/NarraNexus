"""
@file_name: test_health_db_probe.py
@author: Bin Liang
@date: 2026-08-17
@description: `/health` must report the database it actually reached.

The field used to be the literal string `"database": "connected"` — it could
not take any other value. On 2026-08-17 the backend spent 19 minutes failing
every query with `InterfaceError: (0, 'Not connected')` while this endpoint
reported a connected database, the container healthcheck stayed green, and the
monitoring built on that container state never fired.

So the property under test is not "the endpoint returns 200". It is: **when the
database is unreachable, this endpoint says so, and says it loudly enough for a
container probe to notice.**
"""
from __future__ import annotations

import asyncio
import time

import pytest

from loguru import logger

import backend.main as main


class _StubClient:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.queries: list[str] = []

    async def probe(self) -> None:
        self.queries.append("probe")
        await self._behaviour()


def _install(monkeypatch, behaviour) -> _StubClient:
    client = _StubClient(behaviour)

    async def fake_get_db_client():
        return client

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)
    return client


@pytest.mark.asyncio
async def test_reports_healthy_when_the_round_trip_succeeds(monkeypatch):
    async def ok():
        return [{"1": 1}]

    client = _install(monkeypatch, ok)

    body = await main.health()

    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert client.queries == ["probe"], "the probe must actually query the database"


@pytest.mark.asyncio
async def test_reports_unhealthy_and_503_when_the_database_is_gone(monkeypatch):
    """The exact production failure: the pool is dead, every query raises."""

    async def dead():
        raise RuntimeError("(0, 'Not connected')")

    _install(monkeypatch, dead)

    response = await main.health()

    assert response.status_code == 503, (
        "a healthy-looking 200 is what let the outage run unnoticed"
    )
    body = response.body.decode()
    assert '"status":"unhealthy"' in body.replace(" ", "")
    # The CLASS of failure reaches the reader; the driver's own message does
    # not. See the leak test below.
    assert "RuntimeError" in body


@pytest.mark.asyncio
async def test_a_hung_database_becomes_unhealthy_rather_than_a_hung_probe(monkeypatch):
    """A probe that hangs is indistinguishable from a slow one; docker would
    record its own timeout and our reason would be lost. Bound it ourselves."""

    async def hang():
        await asyncio.sleep(10)

    _install(monkeypatch, hang)
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.01)

    response = await main.health()

    assert response.status_code == 503
    assert "timeout" in response.body.decode()


@pytest.mark.asyncio
async def test_the_failure_reason_never_leaks_infrastructure_detail(monkeypatch):
    """`/health` is public and unauthenticated. pymysql renders connect
    failures as "Can't connect to MySQL server on '<rds endpoint>'" and auth
    failures as "Access denied for user '<user>'@'<internal ip>'". Returning
    that verbatim hands anyone who curls during an incident the database host,
    the database user, and an internal IP."""

    async def leaky():
        raise RuntimeError(
            "Can't connect to MySQL server on "
            "'narranexus-cloud-version.cluster-czi0esc0atmh.eu-west-2.rds.amazonaws.com' "
            "(Access denied for user 'nexus'@'172.31.0.4')"
        )

    _install(monkeypatch, leaky)

    response = await main.health()

    body = response.body.decode()
    assert response.status_code == 503
    for secret in ("rds.amazonaws.com", "Access denied", "172.31.", "nexus'@"):
        assert secret not in body, f"{secret!r} leaked to an unauthenticated caller"
    assert "RuntimeError" in body, "the failure class must still be reported"


@pytest.mark.asyncio
async def test_probe_budget_leaves_room_under_the_container_healthcheck_timeout():
    """`stacks/narranexus-app/compose.yml` gives the backend healthcheck
    `timeout: 5s`, and that 5s must also cover the healthcheck's own `python -c`
    interpreter start plus the urllib round-trip. A budget that merely fits
    under 5s would still let docker time out first, replacing our reason with
    its own — the precise blind spot this endpoint was fixed to close."""
    assert main._HEALTH_DB_TIMEOUT_SEC <= 3.0


@pytest.mark.asyncio
async def test_team_summary_failures_still_do_not_fail_the_probe(monkeypatch):
    """The 2026-08-11 rule still holds: a single team with a bad provider key
    must not fail the container. Only the database decides `status`."""

    async def ok():
        return [{"1": 1}]

    _install(monkeypatch, ok)

    class _W:
        running = True
        last_pass = {"rooms": 3, "summarised": 0, "failed": 2}

    main.app.state.team_summary_worker = _W()
    try:
        body = await main.health()
    finally:
        del main.app.state.team_summary_worker

    assert body["status"] == "healthy"
    assert body["team_summary"]["failed"] == 2


@pytest.mark.asyncio
async def test_the_driver_message_survives_in_the_log(monkeypatch):
    """The other half of the promise above: the detail the response withholds
    has to actually reach the log, or withholding it is just data loss.

    This exists because that half regressed once already. The log call sat in
    the `if not db_ok:` branch — outside the `except` — where `logger.exception`
    has no active exception to render and prints a literal "NoneType: None".
    Moving it back out would look tidier and every other test here would stay
    green, which is exactly how it shipped the first time.
    """

    async def dead():
        raise RuntimeError(
            "Can't connect to MySQL server on 'rds-x.internal' "
            "(Access denied for user 'nexus'@'172.31.0.4')"
        )

    _install(monkeypatch, dead)

    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR")
    try:
        response = await main.health()
    finally:
        logger.remove(sink_id)

    logged = "".join(records)
    assert "rds-x.internal" in logged, (
        "the driver's own message is the only place the real cause survives"
    )
    # The load-bearing assertion. The class name appears in the f-string either
    # way, so asserting on it would pass even with the call back outside the
    # except block; only a rendered traceback distinguishes the two.
    assert "Traceback" in logged, (
        "logged outside the except block — renders 'NoneType: None', no cause"
    )
    # And the response still says nothing.
    assert "rds-x.internal" not in response.body.decode()


@pytest.mark.asyncio
async def test_a_timeout_is_also_logged(monkeypatch):
    """`asyncio.TimeoutError` carries no message, so without its own log line a
    timed-out probe leaves not one word behind.

    Asserted on the log output rather than on the source text: reading the
    function body with `inspect.getsource` broke on harmless refactors (writing
    `except TimeoutError:` — the same class since 3.11 — or reordering the
    handlers) and failed with an IndexError that said nothing useful.
    """

    async def hang():
        await asyncio.sleep(10)

    _install(monkeypatch, hang)
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.01)

    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR")
    try:
        response = await main.health()
    finally:
        logger.remove(sink_id)

    assert response.status_code == 503
    assert "timed out after" in "".join(records)


@pytest.mark.asyncio
async def test_a_flood_collapses_onto_one_round_trip(monkeypatch):
    """`/health` is public and unauthenticated, and each probe holds one of the
    backend's ten pooled connections. Without a cache, a few hundred requests a
    second would hold all of them while real traffic queued — an endpoint added
    to make failure visible, turned into a way to cause it."""

    async def ok():
        return None

    client = _install(monkeypatch, ok)

    for _ in range(50):
        await main.health()

    assert len(client.queries) == 1, "every request hit the database"


@pytest.mark.asyncio
async def test_failures_are_cached_too(monkeypatch):
    """Caching only successes would leave the amplification wide open in exactly
    the case where it is dangerous: a database that is down or slow, with every
    request paying the full timeout before answering."""

    async def dead():
        raise RuntimeError("(0, 'Not connected')")

    client = _install(monkeypatch, dead)

    first = await main.health()
    for _ in range(20):
        again = await main.health()
        assert again.status_code == 503

    assert first.status_code == 503
    assert len(client.queries) == 1, "a failing database was re-probed on every request"


@pytest.mark.asyncio
async def test_the_cache_expires(monkeypatch):
    async def ok():
        return None

    client = _install(monkeypatch, ok)

    await main.health()
    assert len(client.queries) == 1

    # Expire it by rewinding the stored deadline rather than sleeping.
    started, deadline, was_ok, detail = main._health_cache
    main._health_cache = (started, deadline - main._HEALTH_CACHE_TTL_SEC - 1, was_ok, detail)

    await main.health()
    assert len(client.queries) == 2


@pytest.mark.asyncio
async def test_the_cache_window_stays_under_the_healthcheck_interval():
    """The container healthcheck runs every 30s and must never be answered from
    cache — it is the signal the container's health is derived from."""
    assert main._HEALTH_CACHE_TTL_SEC < 30.0


@pytest.mark.asyncio
async def test_worker_counters_are_read_fresh_even_on_a_cache_hit(monkeypatch):
    """The cache covers the DB round-trip, not the whole response. Worker
    liveness must not be frozen for 5s alongside it."""

    async def ok():
        return None

    _install(monkeypatch, ok)

    class _W:
        running = True
        last_pass = {"rooms": 1, "summarised": 0, "failed": 0}

    main.app.state.team_summary_worker = _W()
    try:
        await main.health()                      # populates the cache
        _W.last_pass = {"rooms": 9, "summarised": 4, "failed": 1}
        body = await main.health()               # served from cache
    finally:
        del main.app.state.team_summary_worker

    assert body["team_summary"]["rooms"] == 9, "worker counters were frozen by the cache"


@pytest.mark.asyncio
async def test_a_slow_database_is_probed_once_no_matter_how_many_requests_arrive(monkeypatch):
    """The half the cache alone could not close.

    A cache only stops requests arriving AFTER a result is published. Between a
    miss and that publication, every arrival used to run its own probe and hold
    its own pooled connection — and that window is the full timeout precisely
    when the database is slow, which is the case the cache was added for. With
    a 10-connection pool and an unauthenticated endpoint, that is `/health`
    holding the pool while real traffic queues behind it.
    """
    release = asyncio.Event()
    probes = {"n": 0}

    class _Client:
        async def probe(self) -> None:
            probes["n"] += 1
            await release.wait()

    async def fake_get_db_client():
        return _Client()

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)

    callers = [asyncio.create_task(main.health()) for _ in range(20)]
    await asyncio.sleep(0)
    assert probes["n"] == 1, f"{probes['n']} concurrent probes against a slow database"

    release.set()
    await asyncio.gather(*callers)
    assert probes["n"] == 1, "the queued callers each ran their own probe after all"


@pytest.mark.asyncio
async def test_a_probe_that_started_earlier_cannot_overwrite_a_newer_verdict(monkeypatch):
    """The publish guard, kept as a belt to single-flight's braces.

    With the lock in place two probes should not overlap, so this drives
    `_run_health_probe` directly — the guard is what stops a stale verdict if
    that lock is ever removed or bypassed, and it costs two lines to keep.
    """
    state = {"fail": True}
    release_a = asyncio.Event()

    class _Client:
        async def probe(self) -> None:
            if state["fail"]:
                await release_a.wait()
                raise RuntimeError("(0, 'Not connected')")
            return None

    async def fake_get_db_client():
        return _Client()

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)

    a = asyncio.create_task(main._run_health_probe())   # starts first, still in flight
    await asyncio.sleep(0)

    state["fail"] = False                               # database recovers
    await main._run_health_probe()                      # starts later, finishes first
    assert main._health_cache[2] is True

    release_a.set()                                     # A returns its stale failure
    await a

    assert main._health_cache[2] is True, (
        "a probe that started earlier overwrote a newer, healthier verdict"
    )


def test_the_probe_lock_survives_an_event_loop_change():
    """`asyncio.Lock` pins itself to the loop that first CONTENDS it, and raises
    on any later contention from a different one.

    A module-level lock therefore leaks its binding: the first test (or the
    first in-process loop) to run concurrent probes owns it forever. This repo
    does swap loops in-process — `db_factory` carries an eviction path for
    exactly that — and a `/health` that raises becomes a 500, an unhealthy
    container, and a `docker compose up` that fails outright.

    Synchronous on purpose: it drives two loops itself, which cannot be done
    from inside one that is already running.
    """
    seen = []

    async def contend():
        lock = main._probe_lock()
        seen.append(lock)

        async def hold():
            async with lock:          # two waiters, so it takes the contended path
                await asyncio.sleep(0)

        await asyncio.gather(hold(), hold())

    sizes = []
    for _ in range(3):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(contend())
        finally:
            sizes.append(len(main._health_probe_locks))
            loop.close()

    # Objects, not `id()`: the two lifetimes do not overlap, so CPython may well
    # place the second lock at the first one's address.
    assert seen[0] is not seen[1], "the same lock object was reused across loops"

    # The registry holds loops strongly (that is what makes `id()` keys safe),
    # so it must prune dead ones when a new loop registers — otherwise a process
    # that churns loops accumulates one entry per loop forever.
    assert sizes[2] <= sizes[1], f"registry grew unbounded across loops: {sizes}"





@pytest.mark.asyncio
async def test_the_whole_handler_stays_inside_one_budget(monkeypatch):
    """Queue time counts against the budget, not just the probe.

    `_HEALTH_DB_TIMEOUT_SEC` exists only to stay under the container
    healthcheck's `timeout: 5s`, so a failure is recorded as our 503 + reason
    rather than replaced by docker's own "health check timed out". Budgeting
    only the probe let a queued caller spend its wait AND then a full probe.
    """
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.15)

    class _Client:
        async def probe(self) -> None:
            await asyncio.sleep(10)          # never finishes inside the budget

    async def fake_get_db_client():
        return _Client()

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)

    started = time.monotonic()
    results = await asyncio.gather(*(main.health() for _ in range(5)))
    elapsed = time.monotonic() - started

    # Serial would be 5 x 0.15; the holder's timeout publishes and the rest hit
    # the cache, so the whole batch costs about one budget.
    assert elapsed < 0.15 * 3, f"the batch took {elapsed:.2f}s — queue time is unbudgeted"
    assert all(r.status_code == 503 for r in results)


@pytest.mark.asyncio
async def test_a_caller_that_times_out_queueing_does_not_leak_the_lock(monkeypatch):
    """Bounding the acquire replaced `async with`, so the release is now ours to
    get right. A leaked lock would wedge every later probe in the process."""
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.05)

    release = asyncio.Event()

    class _Slow:
        async def probe(self) -> None:
            await release.wait()

    async def fake_get_db_client():
        return _Slow()

    monkeypatch.setattr(main, "get_db_client", fake_get_db_client)

    holder = asyncio.create_task(main.health())
    await asyncio.sleep(0)
    queued = await main.health()          # gives up queueing within the budget
    assert queued.status_code == 503

    release.set()
    await holder

    lock = main._probe_lock()
    assert not lock.locked(), "the lock was left held after a queue timeout"

    # And the process still works afterwards.
    async def ok():
        return None

    _install(monkeypatch, ok)
    main._health_cache = None
    assert (await main.health())["status"] == "healthy"


@pytest.mark.asyncio
async def test_degrading_to_a_stale_result_does_not_extend_its_life(monkeypatch):
    """Answering a queue timeout from the last known verdict must not refresh
    its deadline, or one burst of congestion keeps a stale conclusion alive
    indefinitely.

    The lock is held directly rather than by another `health()` call: a real
    holder would publish its own result on the way out, which is a different
    branch from the one under test.
    """
    monkeypatch.setattr(main, "_HEALTH_DB_TIMEOUT_SEC", 0.05)

    async def ok():
        return None

    _install(monkeypatch, ok)
    await main.health()
    started, _, was_ok, detail = main._health_cache

    # An entry that is already expired, so only the degrade path can serve it.
    stale = (started, time.monotonic() - 1, was_ok, detail)
    main._health_cache = stale

    lock = main._probe_lock()
    await lock.acquire()
    try:
        body = await main.health()         # cannot queue in; must degrade
    finally:
        lock.release()

    assert body["status"] == "healthy", "the stale verdict was not served"
    assert main._health_cache == stale, "the stale entry was rewritten or extended"
