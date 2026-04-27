"""
@file_name: test_temporal_context.py
@author: Bin Liang
@date: 2026-04-21
@description: Tests for the User Temporal Context prompt block.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.context_runtime.prompts import USER_TEMPORAL_CONTEXT


def test_user_temporal_context_template_fields():
    block = USER_TEMPORAL_CONTEXT.format(
        user_tz="Asia/Shanghai",
        now_local="2026-04-21T14:32:00",
    )
    assert "Asia/Shanghai" in block
    assert "2026-04-21T14:32:00" in block
    assert "timezone" in block.lower()


@pytest.mark.asyncio
async def test_build_user_temporal_block_uses_user_timezone(db_client):
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime

    # Seed a user row with a specific timezone
    await db_client.insert("users", {
        "user_id": "u_tz_test",
        "display_name": "tz_user",
        "user_type": "user",
        "timezone": "Asia/Shanghai",
        "status": "active",
    })

    # Minimal ContextRuntime instance — we only exercise the helper
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_unused"

    block = await runtime._build_user_temporal_block("u_tz_test")
    assert "Asia/Shanghai" in block
    # Current date will appear — format is ISO 8601; just check the year
    from datetime import datetime
    year = str(datetime.now().year)
    assert year in block


@pytest.mark.asyncio
async def test_build_user_temporal_block_absent_user_returns_empty(db_client):
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_unused"

    block = await runtime._build_user_temporal_block(None)
    assert block == ""
