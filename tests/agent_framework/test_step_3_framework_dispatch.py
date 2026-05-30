"""
@file_name: test_step_3_framework_dispatch.py
@date: 2026-05-29
@description: Tests for _resolve_agent_framework_sdk in
step_3_agent_loop.py — the per-user dispatch from
user_slots.agent_framework to SDK class.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework import ClaudeAgentSDK, CodexSDK
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _resolve_agent_framework_sdk,
    _AGENT_FRAMEWORK_SDK_MAP,
)


class _FakeDB:
    """Minimal stand-in for AsyncDatabaseClient — only ``get_one`` is
    used by the dispatcher."""

    def __init__(self, row):
        self.row = row
        self.calls: list[tuple] = []

    async def get_one(self, table, filters):
        self.calls.append((table, dict(filters)))
        return self.row


class _DeadDB:
    """DB that always raises on get_one — for the error-fallback test."""

    async def get_one(self, table, filters):
        raise RuntimeError("simulated DB failure")


# ----- happy paths -------------------------------------------------


def test_framework_map_lists_both_sdks():
    assert _AGENT_FRAMEWORK_SDK_MAP["claude_code"] is ClaudeAgentSDK
    assert _AGENT_FRAMEWORK_SDK_MAP["codex_cli"] is CodexSDK


@pytest.mark.asyncio
async def test_dispatch_returns_codex_when_user_chose_codex():
    db = _FakeDB({"agent_framework": "codex_cli"})
    sdk = await _resolve_agent_framework_sdk("u1", db)
    assert sdk is CodexSDK


@pytest.mark.asyncio
async def test_dispatch_returns_claude_when_user_chose_claude():
    db = _FakeDB({"agent_framework": "claude_code"})
    sdk = await _resolve_agent_framework_sdk("u1", db)
    assert sdk is ClaudeAgentSDK


# ----- fallback paths ----------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_row_missing():
    """New user with no user_slots row → ClaudeAgentSDK default."""
    db = _FakeDB(None)
    sdk = await _resolve_agent_framework_sdk("u_new", db)
    assert sdk is ClaudeAgentSDK


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_agent_framework_null():
    """Existing row where agent_framework column is null (pre-migration row)."""
    db = _FakeDB({"agent_framework": None, "provider_id": "p1"})
    sdk = await _resolve_agent_framework_sdk("u1", db)
    assert sdk is ClaudeAgentSDK


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_framework_unknown():
    """Forward-compat: unknown framework name → log warn + claude_code."""
    db = _FakeDB({"agent_framework": "future_framework_X"})
    sdk = await _resolve_agent_framework_sdk("u1", db)
    assert sdk is ClaudeAgentSDK


@pytest.mark.asyncio
async def test_dispatch_falls_back_on_db_error():
    """Any DB error (connection lost, etc) → defensive claude_code."""
    sdk = await _resolve_agent_framework_sdk("u1", _DeadDB())
    assert sdk is ClaudeAgentSDK


# ----- DB query shape ----------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_queries_user_slots_with_agent_slot_filter():
    """Pin down that we read from the right table + filter shape."""
    db = _FakeDB({"agent_framework": "codex_cli"})
    await _resolve_agent_framework_sdk("u123", db)
    assert db.calls == [("user_slots", {"user_id": "u123", "slot_name": "agent"})]
