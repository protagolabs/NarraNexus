"""
@file_name: test_first_cycle_on_a_fresh_host.py
@author: NarraNexus
@date: 2026-08-17
@description: "Never ran yet" must not be spelled 0.0 against a monotonic clock.

`time.monotonic()` counts from BOOT on Linux, so a `_last_*_monotonic = 0.0`
sentinel does not mean "never ran" — it means "ran at boot". Every gate of the
form `monotonic() - mark >= interval` therefore stays silently shut until the
HOST has been up longer than the interval:

  * channel-trigger heartbeat (600s) — no L2 liveness row for the first ten
    minutes of a freshly booted host, which is exactly the window where a
    failing start is most likely and exactly what incident lesson #4 wants
    those beats to cover.
  * channel-trigger retention sweep (24h) — `_run_cleanup` documents itself as
    "once at startup + daily"; a new EC2 instance got neither until its uptime
    reached a day.
  * `ServiceAuditor.heartbeat` (60s) — same shape, small window.

None of this is visible on a developer laptop or a long-lived EC2, where uptime
is measured in weeks: the gate always looks open. It surfaced from the other
end — `test_credential_breaker.py::test_heartbeat_carries_isolation_counts`
used the same `0.0` idiom and passed everywhere except on a fresh CI runner,
where it indexed an empty list (2026-08-17). The test was one instance; these
three were the same assumption in production.

Every test here pretends the host just booted. That is the only condition under
which the difference between `0.0` and `-inf` is observable, which is why
nothing caught it before.
"""
from __future__ import annotations

import time

import pytest

from xyz_agent_context.services.service_audit import ServiceAuditor


@pytest.fixture
def freshly_booted(monkeypatch):
    """`time.monotonic()` a few seconds after boot — below every interval here."""
    real = time.monotonic
    base = real()

    def _just_booted() -> float:
        return real() - base + 5.0

    monkeypatch.setattr(time, "monotonic", _just_booted)
    return _just_booted


def test_the_auditor_sentinel_is_not_a_small_number():
    """Guarding the idiom itself, not only its effect.

    A future edit that "tidies" this back to 0.0 reopens a gap that no test on a
    long-uptime machine can observe, so the spelling is asserted directly.
    """
    assert ServiceAuditor("svc")._last_heartbeat_at == float("-inf"), (
        "0.0 is not 'never beat' against a monotonic clock — it is 'beat at boot'"
    )


@pytest.mark.asyncio
async def test_a_service_auditor_beats_on_its_very_first_call(freshly_booted):
    """A 60-second gate must not depend on the host being 60 seconds old."""
    auditor = ServiceAuditor("svc-under-test")
    emitted: list = []

    async def _capture(event, detail):
        emitted.append(event)

    auditor._emit = _capture

    await auditor.heartbeat()

    assert emitted, (
        "the first heartbeat was skipped on a host younger than the interval — "
        "no liveness signal during the window a bad start is most likely"
    )


@pytest.mark.asyncio
async def test_the_channel_trigger_marks_start_open(freshly_booted):
    """Both channel-trigger gates must be open on the first cycle.

    Asserted on the marks rather than by driving the run loop: the loop needs a
    live queue, workers and an audit repo, and what rotted here was the initial
    value, not the loop.
    """
    import inspect

    from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase

    # The class is abstract (six subclass hooks), and what rotted here is the
    # initial value rather than any behaviour reachable without a live queue,
    # workers and an audit repo. So: read the value the constructor installs,
    # then check the gate arithmetic against it with a freshly-booted clock.
    src = inspect.getsource(ChannelTriggerBase.__init__)
    assert 'self._last_cleanup_monotonic: float = float("-inf")' in src, (
        "the retention-sweep mark is no longer a real 'never ran' sentinel"
    )
    assert 'self._last_heartbeat_monotonic: float = float("-inf")' in src, (
        "the heartbeat mark is no longer a real 'never ran' sentinel"
    )

    now = time.monotonic()  # seconds after boot, per the fixture
    assert now < ChannelTriggerBase.HEARTBEAT_INTERVAL_SECONDS, (
        "the fixture is meant to put us inside the interval, or this proves nothing"
    )
    assert now - float("-inf") >= ChannelTriggerBase.HEARTBEAT_INTERVAL_SECONDS
    assert now - float("-inf") >= ChannelTriggerBase.CLEANUP_INTERVAL_SECONDS
    # The value it replaced would have failed both, which is the whole point.
    assert not (now - 0.0 >= ChannelTriggerBase.HEARTBEAT_INTERVAL_SECONDS)
    assert not (now - 0.0 >= ChannelTriggerBase.CLEANUP_INTERVAL_SECONDS)
