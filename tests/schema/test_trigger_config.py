"""
@file_name: test_trigger_config.py
@author: Bin Liang
@date: 2026-04-21
@description: Validator tests for TriggerConfig timezone protocol (v2).
"""
import pytest
from datetime import datetime
from pydantic import ValidationError

from xyz_agent_context.schema.job_schema import TriggerConfig


class TestTriggerConfigTimezoneRequired:
    def test_one_off_requires_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(run_at=datetime(2026, 5, 1, 8, 0, 0))

    def test_cron_requires_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(cron="0 8 * * *")

    def test_interval_requires_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            TriggerConfig(interval_seconds=3600)

    def test_valid_one_off(self):
        tc = TriggerConfig(
            run_at=datetime(2026, 5, 1, 8, 0, 0),
            timezone="Asia/Shanghai",
        )
        assert tc.timezone == "Asia/Shanghai"

    def test_valid_cron(self):
        tc = TriggerConfig(cron="0 8 * * *", timezone="America/New_York")
        assert tc.timezone == "America/New_York"

    def test_valid_interval(self):
        tc = TriggerConfig(interval_seconds=3600, timezone="UTC")
        assert tc.timezone == "UTC"
        assert tc.interval_seconds == 3600


class TestTriggerConfigRunAtNaive:
    def test_rejects_aware_run_at(self):
        from datetime import timezone as dt_tz
        aware = datetime(2026, 5, 1, 8, 0, 0, tzinfo=dt_tz.utc)
        with pytest.raises(ValidationError, match="naive"):
            TriggerConfig(run_at=aware, timezone="Asia/Shanghai")


class TestTriggerConfigIANAValid:
    def test_rejects_invalid_iana(self):
        with pytest.raises(ValidationError, match="not a valid IANA"):
            TriggerConfig(cron="0 8 * * *", timezone="CST")

    def test_rejects_empty_timezone(self):
        with pytest.raises(ValidationError):
            TriggerConfig(cron="0 8 * * *", timezone="")


class TestJobEntityNewFields:
    def test_job_has_beta_fields(self):
        from xyz_agent_context.schema.job_schema import Job
        fields = Job.model_fields
        assert "next_run_at_local" in fields
        assert "next_run_tz" in fields
        assert "last_run_at_local" in fields
        assert "last_run_tz" in fields
