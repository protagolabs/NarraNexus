"""
@file_name: _job_scheduling.py
@author: Bin Liang
@date: 2026-03-06
@description: Job scheduling utility functions

Extracted from job_repository.py. Contains business logic for
calculating next execution times based on job type and trigger config.

This logic belongs in the module layer (not repository) because it
encodes scheduling rules rather than data access patterns.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema.job_schema import JobType, TriggerConfig


def calculate_next_run_time(
    job_type: JobType,
    trigger_config: TriggerConfig,
    last_run_time: Optional[datetime] = None
) -> Optional[datetime]:
    """
    Calculate the next execution time for a task

    Supports four trigger types:
    1. ONE_OFF + run_at: Execute at a specified time
    2. SCHEDULED + cron: Execute by cron expression (requires croniter)
    3. SCHEDULED + interval_seconds: Execute at fixed intervals
    4. ONGOING + interval_seconds: Execute at fixed intervals until end condition is met

    Args:
        job_type: Task type (ONE_OFF, SCHEDULED, or ONGOING)
        trigger_config: Trigger configuration object
        last_run_time: Last execution time (used for interval calculation)

    Returns:
        Next execution time, or None if not applicable

    Example:
        # One-off task
        next_time = calculate_next_run_time(
            JobType.ONE_OFF,
            TriggerConfig(run_at=datetime(2025, 1, 16, 8, 0))
        )

        # Cron task (daily at 8 AM)
        next_time = calculate_next_run_time(
            JobType.SCHEDULED,
            TriggerConfig(cron="0 8 * * *")
        )

        # Interval task (every hour)
        next_time = calculate_next_run_time(
            JobType.SCHEDULED,
            TriggerConfig(interval_seconds=3600),
            last_run_time=utc_now()
        )

        # ONGOING task (check every hour until condition is met)
        next_time = calculate_next_run_time(
            JobType.ONGOING,
            TriggerConfig(interval_seconds=3600, end_condition="Customer completes purchase"),
            last_run_time=utc_now()
        )
    """
    if job_type == JobType.ONE_OFF:
        return trigger_config.run_at

    elif job_type == JobType.SCHEDULED:
        if trigger_config.cron:
            try:
                from croniter import croniter
                base_time = last_run_time or utc_now()
                cron = croniter(trigger_config.cron, base_time)
                return cron.get_next(datetime)
            except ImportError:
                logger.warning(
                    "croniter package not installed. "
                    "Install with: pip install croniter"
                )
                return utc_now() + timedelta(hours=1)
            except Exception as e:
                logger.error(f"Failed to parse cron expression '{trigger_config.cron}': {e}")
                return None

        elif trigger_config.interval_seconds:
            base_time = last_run_time or utc_now()
            return base_time + timedelta(seconds=trigger_config.interval_seconds)

    elif job_type == JobType.ONGOING:
        if trigger_config.interval_seconds:
            base_time = last_run_time or utc_now()
            return base_time + timedelta(seconds=trigger_config.interval_seconds)

    return None


# =============================================================================
# New atomic scheduling API (Task 2: 2026-04-21)
# =============================================================================

@dataclass(frozen=True)
class NextRunTuple:
    """
    Atomic result of compute_next_run().

    `local` and `utc` are the SAME physical instant expressed in two coordinate
    systems; `tz` is the IANA name of the user-facing coordinate system.
    """
    local: str       # naive ISO 8601, e.g. "2026-05-01T08:00:00"
    tz: str          # IANA, e.g. "Asia/Shanghai"
    utc: datetime    # aware UTC datetime, for poller WHERE clauses


def compute_next_run(
    job_type: JobType,
    trigger_config: TriggerConfig,
    last_run_utc: Optional[datetime] = None,
) -> Optional[NextRunTuple]:
    """
    Single source of truth for "when does this job fire next".

    Returns the fire instant as a (local, tz, utc) triple.

    For one_off: uses trigger_config.run_at (naive) + trigger_config.timezone.
    For scheduled + cron: croniter steps forward from last_run_utc (or utc_now)
        in the job's timezone.
    For scheduled/ongoing + interval: last_run_utc + interval, localized for display.

    Returns None only when the trigger_config carries no fireable time
    (e.g. one_off with run_at=None, or trigger lacks any time field).
    Callers decide terminal-state behavior (one_off post-fire, ongoing end_condition).
    """
    tz_name = trigger_config.timezone
    if tz_name is None:
        # TriggerConfig validator should have caught this; defensive re-check.
        raise ValueError(
            "compute_next_run requires trigger_config.timezone; this should be "
            "enforced by the TriggerConfig validator"
        )
    zi = ZoneInfo(tz_name)

    if job_type == JobType.ONE_OFF:
        if trigger_config.run_at is None:
            return None
        local_dt = trigger_config.run_at  # naive, enforced by validator
        aware_local = local_dt.replace(tzinfo=zi)
        utc_dt = aware_local.astimezone(dt_timezone.utc)
        return NextRunTuple(
            local=local_dt.isoformat(),
            tz=tz_name,
            utc=utc_dt,
        )

    if job_type in (JobType.SCHEDULED, JobType.ONGOING):
        base_utc = last_run_utc if last_run_utc is not None else utc_now()
        if trigger_config.cron:
            from croniter import croniter
            # Use naive local time as croniter base so DST transitions are
            # handled in wall-clock space (not UTC-offset space).  croniter
            # with an aware datetime applies DST-fold logic that produces the
            # wrong hour on transition nights (e.g. "8am" becomes "9am" in EST
            # when the base was already past 8am in EDT).  Passing naive keeps
            # the "next 8am" meaning correct, and zoneinfo.replace() resolves
            # the UTC offset correctly on the output side.
            base_local_naive = base_utc.astimezone(zi).replace(tzinfo=None)
            cron = croniter(trigger_config.cron, base_local_naive)
            next_local_naive = cron.get_next(datetime)  # always naive
            # Attach tz — zoneinfo correctly handles DST fold for the result date
            next_local_aware = next_local_naive.replace(tzinfo=zi)
            next_utc = next_local_aware.astimezone(dt_timezone.utc)
            return NextRunTuple(
                local=next_local_naive.isoformat(),
                tz=tz_name,
                utc=next_utc,
            )
        if trigger_config.interval_seconds:
            next_utc = base_utc + timedelta(seconds=trigger_config.interval_seconds)
            next_local = next_utc.astimezone(zi).replace(tzinfo=None)
            return NextRunTuple(
                local=next_local.isoformat(),
                tz=tz_name,
                utc=next_utc,
            )

    return None
