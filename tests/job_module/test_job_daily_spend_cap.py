"""
@file_name: test_job_daily_spend_cap.py
@author: Bin Liang
@date: 2026-09-09
@description: B-14 — user-level daily spend circuit for scheduled/ongoing
jobs. A user burned ~$140 in 4 days on two 2h heartbeat jobs under
nexus_power (1-7M input tokens/run). NARRANEXUS_JOB_DAILY_SPEND_CAP_USD
(0/unset = disabled) gates the NEXT scheduled start — it never interrupts a
run already in flight.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_tz

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus
from narranexus_plugins.job_module.job_trigger import (
    JobTrigger,
    _daily_spend_usd_for_user,
)

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'
ENV_VAR = "NARRANEXUS_JOB_DAILY_SPEND_CAP_USD"


async def _insert_job(db, job_id, user_id="user_1", status="active"):
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": user_id,
        "title": "Heartbeat job", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": SCHEDULED_TRIGGER,
        "status": status,
        "notification_method": "inbox",
        "next_run_time": "2020-01-01T00:00:00Z",
        "created_at": now,
        "updated_at": now,
    })


async def _insert_cost_record(db, user_id, cost_usd, created_at=None):
    await db.insert("cost_records", {
        "agent_id": "agent_1",
        "call_type": "agent_loop",
        "model": "test-model",
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_cost_usd": cost_usd,
        "user_id": user_id,
        "created_at": created_at or datetime.now(dt_tz.utc),
    })


# ── pure sum helper (dialect-portable raw SQL) ──────────────────────────────

@pytest.mark.asyncio
async def test_sums_only_todays_records_for_the_user(db_client):
    await _insert_cost_record(db_client, "user_1", 1.5)
    await _insert_cost_record(db_client, "user_1", 2.25)
    # Yesterday — must not count.
    yesterday = datetime.now(dt_tz.utc) - timedelta(days=1)
    await _insert_cost_record(db_client, "user_1", 100.0, created_at=yesterday)
    # A different user — must not count.
    await _insert_cost_record(db_client, "user_2", 50.0)

    total = await _daily_spend_usd_for_user(db_client, "user_1")

    assert total == pytest.approx(3.75)


@pytest.mark.asyncio
async def test_no_records_returns_zero(db_client):
    assert await _daily_spend_usd_for_user(db_client, "user_nobody") == 0.0


# ── cap gate + pause wiring ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_by_default(db_client, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    await _insert_cost_record(db_client, "user_1", 99999.0)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._daily_spend_cap_exceeded("user_1") is False


@pytest.mark.asyncio
async def test_cap_exceeded_when_spend_meets_the_threshold(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "10")
    await _insert_cost_record(db_client, "user_1", 10.0)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._daily_spend_cap_exceeded("user_1") is True


@pytest.mark.asyncio
async def test_cap_not_exceeded_when_under_threshold(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "10")
    await _insert_cost_record(db_client, "user_1", 5.0)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._daily_spend_cap_exceeded("user_1") is False


@pytest.mark.asyncio
async def test_execute_job_pauses_with_spend_cap_status(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")
    await _insert_cost_record(db_client, "user_1", 5.0)
    await _insert_job(db_client, "job_over_cap")
    repo = JobRepository(db_client)
    job = await repo.get_job("job_over_cap")
    trigger = JobTrigger(database_client=db_client)

    async def _boom(*a, **k):
        raise AssertionError("must not call the framework once the cap is exceeded")
    monkeypatch.setattr(trigger, "_run_agent", _boom)

    await trigger._execute_job(job)

    row = await db_client.get_one("instance_jobs", {"job_id": "job_over_cap"})
    assert row["status"] == JobStatus.PAUSED_SPEND_CAP.value
    assert row["paused_reason"] == "spend_cap"


@pytest.mark.asyncio
async def test_execute_job_runs_normally_under_cap(db_client, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "100")
    await _insert_cost_record(db_client, "user_1", 5.0)
    await _insert_job(db_client, "job_under_cap")
    repo = JobRepository(db_client)
    job = await repo.get_job("job_under_cap")
    trigger = JobTrigger(database_client=db_client)

    called = {}

    async def _fake_run_agent(job_arg, prompt):
        called["ran"] = True
        return {"success": True, "output": "ok", "event_id": None}

    monkeypatch.setattr(trigger, "_run_agent", _fake_run_agent)

    await trigger._execute_job(job)

    assert called.get("ran") is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_under_cap"})
    assert row["status"] != JobStatus.PAUSED_SPEND_CAP.value
