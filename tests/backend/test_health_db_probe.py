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

import pytest

from loguru import logger

import backend.main as main


class _StubClient:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.queries: list[str] = []

    async def execute(self, query, params=None, fetch=True):
        self.queries.append(query)
        return await self._behaviour()


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
    assert client.queries == ["SELECT 1"], "the probe must actually query the database"


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
async def test_a_timeout_is_also_logged():
    """`asyncio.TimeoutError` carries no message, so without its own log line a
    timed-out probe leaves not one word behind."""
    import inspect

    src = inspect.getsource(main.health)
    timeout_branch = src.split("except asyncio.TimeoutError:")[1].split("except Exception")[0]
    assert "logger" in timeout_branch
