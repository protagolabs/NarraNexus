"""
@file_name: test_job_pause_inbox_notification.py
@author: Bin Liang
@date: 2026-09-09
@description: B-17 — a job paused for quota/auth exhaustion must notify the
owner via the existing inbox mechanism. Before this fix, `_finalize_job_execution`
only wrote a `logger.warning` on pause — the user had no in-product signal
that a scheduled job silently stopped running (only visible on the Jobs
panel if they happened to look).
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_tz

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.repository.inbox_repository import InboxRepository
from narranexus.platform.schema.job_schema import JobStatus
from narranexus_plugins.job_module.job_trigger import JobTrigger

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _insert_job(db, job_id, user_id="user_1", status="active"):
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": user_id,
        "title": "Daily NetMind report", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": SCHEDULED_TRIGGER,
        "status": status,
        "notification_method": "inbox",
        "created_at": now,
        "updated_at": now,
    })


@pytest.mark.asyncio
async def test_quota_pause_writes_an_inbox_notice(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_q_notify")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_q_notify")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "NoProviderConfiguredError",
        "error": "No provider configured.",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_q_notify"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 1
    msg = messages[0]
    assert "Daily NetMind report" in msg.title
    assert "job_q_notify" in msg.source.id


@pytest.mark.asyncio
async def test_transient_cooling_does_not_notify(db_client):
    """A transient failure (COOLING, not paused) is not owner-actionable
    yet — no notification noise for something that self-heals."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_transient")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_transient")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "APITimeoutError",
        "error": "Read timed out",
        "event_id": None,
    })

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert messages == []


@pytest.mark.asyncio
async def test_notification_failure_does_not_break_the_pause(db_client, monkeypatch):
    """Best-effort: an inbox write failure must not prevent the job from
    still being correctly paused."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_notify_fails")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_notify_fails")

    async def _boom(*args, **kwargs):
        raise RuntimeError("inbox db down")

    monkeypatch.setattr(InboxRepository, "create_message", _boom)

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "NoProviderConfiguredError",
        "error": "No provider configured.",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_notify_fails"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value
