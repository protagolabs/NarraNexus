"""Regression coverage for the dashboard status response contract."""

import pytest

from backend.routes.dashboard._schema import PendingJob, QueueCounts


@pytest.mark.parametrize(
    "queue_status",
    ["cooling", "paused_no_quota", "blocked_failed"],
)
def test_pending_job_accepts_recoverable_queue_states(queue_status: str) -> None:
    job = PendingJob(
        job_id="job-1",
        title="Recoverable job",
        job_type="scheduled",
        next_run_at="2026-08-25T10:00:00",
        next_run_timezone="Europe/London",
        queue_status=queue_status,
    )

    payload = job.model_dump()

    assert payload["queue_status"] == queue_status
    assert payload["next_run_at"] == "2026-08-25T10:00:00"
    assert payload["next_run_timezone"] == "Europe/London"


def test_queue_counts_preserve_all_live_states() -> None:
    queue = QueueCounts(
        running=0,
        active=0,
        pending=0,
        blocked=0,
        paused=0,
        failed=0,
        cooling=1,
        paused_no_quota=2,
        blocked_failed=3,
        total=6,
    )

    payload = queue.model_dump()

    assert payload["cooling"] == 1
    assert payload["paused_no_quota"] == 2
    assert payload["blocked_failed"] == 3
