"""
@file_name: test_one_shot_migrations.py
@author: Bin Liang
@date: 2026-04-21
"""
import json
import pytest

from xyz_agent_context.utils.one_shot_migrations import migrate_jobs_protocol_v2_timezone


@pytest.mark.asyncio
async def test_cancels_old_protocol_active_jobs(db_client):
    # Old protocol row (no timezone)
    await db_client.insert("instance_jobs", {
        "job_id": "old_1",
        "instance_id": "ins_old_1",
        "agent_id": "a1", "user_id": "u1",
        "title": "old", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": json.dumps({"cron": "0 8 * * *"}),
        "status": "active",
        "notification_method": "inbox",
    })
    # New protocol row (with timezone)
    await db_client.insert("instance_jobs", {
        "job_id": "new_1",
        "instance_id": "ins_new_1",
        "agent_id": "a1", "user_id": "u1",
        "title": "new", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": json.dumps({"cron": "0 8 * * *", "timezone": "Asia/Shanghai"}),
        "status": "active",
        "notification_method": "inbox",
    })
    stats = await migrate_jobs_protocol_v2_timezone(db_client)
    assert stats["cancelled"] == 1

    old_row = await db_client.get_one("instance_jobs", {"job_id": "old_1"})
    assert old_row["status"] == "cancelled"
    assert "Protocol migration" in (old_row["last_error"] or "")
    assert old_row["next_run_time"] is None

    new_row = await db_client.get_one("instance_jobs", {"job_id": "new_1"})
    assert new_row["status"] == "active"


@pytest.mark.asyncio
async def test_idempotent(db_client):
    await db_client.insert("instance_jobs", {
        "job_id": "old_2", "instance_id": "ins_old_2",
        "agent_id": "a1", "user_id": "u1",
        "title": "x", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": json.dumps({"cron": "0 8 * * *"}),
        "status": "active", "notification_method": "inbox",
    })
    await migrate_jobs_protocol_v2_timezone(db_client)
    stats2 = await migrate_jobs_protocol_v2_timezone(db_client)
    assert stats2["cancelled"] == 0


@pytest.mark.asyncio
async def test_completed_jobs_untouched(db_client):
    """Migration only targets active/pending/paused; terminal states stay."""
    await db_client.insert("instance_jobs", {
        "job_id": "done_1", "instance_id": "ins_done_1",
        "agent_id": "a1", "user_id": "u1",
        "title": "x", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": json.dumps({"cron": "0 8 * * *"}),  # no timezone, but completed
        "status": "completed", "notification_method": "inbox",
    })
    stats = await migrate_jobs_protocol_v2_timezone(db_client)
    assert stats["cancelled"] == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "done_1"})
    assert row["status"] == "completed"
