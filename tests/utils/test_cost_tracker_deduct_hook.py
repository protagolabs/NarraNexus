"""
@file_name: test_cost_tracker_deduct_hook.py
@author: Bin Liang
@date: 2026-04-16
@description: record_cost calls QuotaService.default().deduct only when
provider_source ContextVar is "system". All other cases (user / None /
missing current_user_id / QuotaService not initialized) must be silent
no-ops that do NOT affect the cost_records insert.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.api_config import (
    set_current_user_id,
    set_provider_source,
)
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.utils.cost_tracker import record_cost


@pytest.fixture(autouse=True)
def _reset_ctx():
    set_provider_source(None)
    set_current_user_id(None)
    QuotaService._default = None
    yield
    set_provider_source(None)
    set_current_user_id(None)
    QuotaService._default = None


def _mk_mock_db():
    m = MagicMock()
    m.insert = AsyncMock(return_value=1)
    return m


@pytest.mark.asyncio
async def test_no_deduct_when_source_is_user():
    set_provider_source("user")
    set_current_user_id("usr_x")
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    deduct.assert_not_called()


@pytest.mark.asyncio
async def test_no_deduct_when_source_is_none():
    set_provider_source(None)
    set_current_user_id("usr_x")
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    deduct.assert_not_called()


@pytest.mark.asyncio
async def test_no_deduct_when_user_id_missing():
    """System tag without a user id — safe fallback: don't deduct."""
    set_provider_source("system")
    set_current_user_id(None)
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    deduct.assert_not_called()


@pytest.mark.asyncio
async def test_no_deduct_when_quota_service_not_initialized():
    """set_default never called -> hook must silently skip, not raise."""
    set_provider_source("system")
    set_current_user_id("usr_x")

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    # If this raises, the test fails — we assert by reaching here.


@pytest.mark.asyncio
async def test_deduct_called_with_exact_tokens_when_source_is_system():
    set_provider_source("system")
    set_current_user_id("usr_y")
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=77,
        output_tokens=11,
    )
    deduct.assert_awaited_once_with("usr_y", 77, 11)


@pytest.mark.asyncio
async def test_deduct_exceptions_swallowed():
    """Deduct failures must not propagate — cost_tracker is observability, not control."""
    set_provider_source("system")
    set_current_user_id("usr_z")

    async def boom(*a, **k):
        raise RuntimeError("db down")
    fake_svc = MagicMock()
    fake_svc.deduct = boom
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=1,
        output_tokens=1,
    )  # must not raise
