"""
@file_name: test_schedule_horizon.py
@author: Bin Liang
@date: 2026-08-19
@description: TriggerConfig.end_at — the platform-enforced scheduling horizon
for recurring jobs ("this schedule runs until date X"). Born with the
onboarding guide agent's daily check-in (whose only hard stop used to be a
sentence in the payload asking the model to pause itself), but generic:
trial-period reminders, countdowns, N-day courses all need the same
primitive. Pins: the schema contract (naive + timezone required), the pure
horizon predicate, and the trigger finalize path completing — not
rescheduling — a job whose next fire lands past its horizon, while a
horizon-free job behaves exactly as before.
"""
from datetime import datetime, timezone as dt_tz

import pytest

from xyz_agent_context.module.job_module._job_scheduling import past_schedule_horizon
from xyz_agent_context.module.job_module.job_trigger import JobTrigger
from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus, TriggerConfig


# ── schema contract ──────────────────────────────────────────────────────────

def test_end_at_must_be_naive():
    with pytest.raises(ValueError, match="naive"):
        TriggerConfig(
            interval_seconds=86400,
            timezone="UTC",
            end_at=datetime(2026, 9, 2, tzinfo=dt_tz.utc),
        )


def test_end_at_alone_requires_timezone():
    with pytest.raises(ValueError, match="timezone is required"):
        TriggerConfig(end_at=datetime(2026, 9, 2))


def test_end_at_is_optional_and_defaults_none():
    tc = TriggerConfig(interval_seconds=3600, timezone="UTC")
    assert tc.end_at is None  # pre-existing configs parse unchanged


# ── horizon predicate ────────────────────────────────────────────────────────

def test_past_schedule_horizon_predicate():
    tc = TriggerConfig(
        interval_seconds=86400,
        timezone="Asia/Shanghai",
        end_at=datetime(2026, 9, 2, 8, 0, 0),  # naive local (UTC+8) = 00:00 UTC
    )
    assert past_schedule_horizon(tc, datetime(2026, 9, 2, 1, 0, tzinfo=dt_tz.utc)) is True
    assert past_schedule_horizon(tc, datetime(2026, 9, 1, 23, 0, tzinfo=dt_tz.utc)) is False
    # No horizon / no config → never trips (pre-existing jobs untouched).
    assert past_schedule_horizon(
        TriggerConfig(interval_seconds=60, timezone="UTC"),
        datetime(2099, 1, 1, tzinfo=dt_tz.utc),
    ) is False
    assert past_schedule_horizon(None, datetime(2099, 1, 1, tzinfo=dt_tz.utc)) is False


# ── trigger finalize path ────────────────────────────────────────────────────

async def _insert_scheduled_job(db, job_id, trigger_config: str):
    now = datetime(2026, 8, 19, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": trigger_config,
        "status": "running", "notification_method": "inbox",
        "created_at": now, "updated_at": now,
    })


@pytest.mark.asyncio
async def test_finalize_completes_scheduled_job_past_horizon(db_client):
    # end_at is in the past, so the next fire (now + 1 day) is past the
    # horizon: the platform must complete the job, NOT reschedule it.
    await _insert_scheduled_job(
        db_client, "job_h1",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2026-01-01T00:00:00"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_h1")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h1"})
    assert row["status"] == JobStatus.COMPLETED.value
    assert row["next_run_time"] is None  # nothing left for the poller to fire


@pytest.mark.asyncio
async def test_finalize_reschedules_before_horizon(db_client):
    # Horizon far in the future: behavior identical to a horizon-free job.
    await _insert_scheduled_job(
        db_client, "job_h2",
        '{"interval_seconds":86400,"timezone":"UTC","end_at":"2099-01-01T00:00:00"}',
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_h2")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h2"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["next_run_time"] is not None


@pytest.mark.asyncio
async def test_finalize_without_horizon_is_unchanged(db_client):
    await _insert_scheduled_job(
        db_client, "job_h3", '{"interval_seconds":86400,"timezone":"UTC"}'
    )
    trigger = JobTrigger(database_client=db_client)
    job = await JobRepository(db_client).get_job("job_h3")

    await trigger._finalize_job_execution(
        job, {"success": True, "event_id": None, "content": "ok"}
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_h3"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["next_run_time"] is not None
