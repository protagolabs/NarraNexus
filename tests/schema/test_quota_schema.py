"""
@file_name: test_quota_schema.py
@author: Bin Liang
@date: 2026-04-16
@description: Unit tests for Quota Pydantic model.
"""
from datetime import datetime, timezone
import pytest
from xyz_agent_context.schema.quota_schema import Quota, QuotaStatus


def _make_quota(**overrides):
    defaults = dict(
        user_id="usr_test",
        initial_input_tokens=1000,
        initial_output_tokens=500,
        used_input_tokens=0,
        used_output_tokens=0,
        granted_input_tokens=0,
        granted_output_tokens=0,
        status=QuotaStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Quota(**defaults)


def test_remaining_input_basic():
    q = _make_quota(used_input_tokens=300)
    assert q.remaining_input == 700


def test_remaining_input_with_grant():
    q = _make_quota(granted_input_tokens=500, used_input_tokens=200)
    assert q.remaining_input == 1300


def test_remaining_input_clamps_to_zero_when_overdrawn():
    q = _make_quota(used_input_tokens=1500)
    assert q.remaining_input == 0


def test_remaining_output_same_math_as_input():
    q = _make_quota(
        initial_output_tokens=500,
        granted_output_tokens=100,
        used_output_tokens=400,
    )
    assert q.remaining_output == 200


def test_has_budget_true_when_both_remain():
    q = _make_quota(used_input_tokens=100, used_output_tokens=100)
    assert q.has_budget() is True


def test_has_budget_false_when_input_exhausted():
    q = _make_quota(used_input_tokens=1000)
    assert q.has_budget() is False


def test_has_budget_false_when_output_exhausted():
    q = _make_quota(used_output_tokens=500)
    assert q.has_budget() is False


def test_has_budget_false_when_status_disabled():
    q = _make_quota(status=QuotaStatus.DISABLED)
    assert q.has_budget() is False


def test_has_budget_false_when_status_exhausted():
    q = _make_quota(status=QuotaStatus.EXHAUSTED)
    assert q.has_budget() is False
