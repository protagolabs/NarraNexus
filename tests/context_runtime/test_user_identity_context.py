"""
@file_name: test_user_identity_context.py
@author: NarraNexus
@date: 2026-06-11
@description: Tests for the User Identity Context prompt block — the clean
separation of user_id (opaque scoping key) from the human display name in
what the agent's LLM reads.

The block always states the agent's owner (by display name), and — for the
chat trigger, which puts `sender_user_id` into extra_data — additionally
states who sent the current message and whether they are the owner. IM
triggers don't set `sender_user_id` (their own module trust block handles
the sender), so they get only the owner line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pytest

from xyz_agent_context.context_runtime.prompts import USER_IDENTITY_CONTEXT


@dataclass
class _Ctx:
    """Minimal stand-in for ContextData (only fields the helper reads)."""
    user_id: str
    extra_data: Dict[str, Any] = field(default_factory=dict)


def test_user_identity_template_has_owner_slot():
    block = USER_IDENTITY_CONTEXT.format(owner_name="Alice", sender_line="")
    assert "Alice" in block
    assert "belongs to" in block.lower()


async def _seed(db, user_id, display_name):
    await db.insert("users", {
        "user_id": user_id,
        "display_name": display_name,
        "user_type": "individual",
        "status": "active",
    })


def _runtime(db_client):
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
    rt = ContextRuntime.__new__(ContextRuntime)
    rt.db = db_client
    rt.agent_id = "agent_unused"
    return rt


@pytest.mark.asyncio
async def test_owner_only_when_no_sender(db_client):
    # IM / job / bus path: no sender_user_id in extra_data -> just the owner line.
    await _seed(db_client, "owner_code", "Alice")
    rt = _runtime(db_client)
    block = await rt._build_user_identity_block(_Ctx(user_id="owner_code"))
    assert "Alice" in block
    assert "belongs to" in block.lower()
    # No visitor/sender line.
    assert "NOT the owner" not in block


@pytest.mark.asyncio
async def test_chat_sender_is_owner(db_client):
    # Chat where the logged-in sender IS the owner (the common case).
    await _seed(db_client, "owner_code", "Alice")
    rt = _runtime(db_client)
    ctx = _Ctx(user_id="owner_code", extra_data={"sender_user_id": "owner_code"})
    block = await rt._build_user_identity_block(ctx)
    assert "Alice" in block
    assert "owner" in block.lower()
    assert "NOT the owner" not in block


@pytest.mark.asyncio
async def test_chat_sender_is_visitor(db_client):
    # Chat on a public agent where the sender is someone other than the owner.
    await _seed(db_client, "owner_code", "Alice")
    await _seed(db_client, "visitor_code", "Bob")
    rt = _runtime(db_client)
    ctx = _Ctx(user_id="owner_code", extra_data={"sender_user_id": "visitor_code"})
    block = await rt._build_user_identity_block(ctx)
    assert "Alice" in block          # owner still named
    assert "Bob" in block            # visitor named (their display_name)
    assert "NOT the owner" in block


@pytest.mark.asyncio
async def test_owner_name_falls_back_to_user_id(db_client):
    # A user row with no display_name -> fall back to the user_id itself.
    await db_client.insert("users", {
        "user_id": "bare_code", "user_type": "individual", "status": "active",
    })
    rt = _runtime(db_client)
    block = await rt._build_user_identity_block(_Ctx(user_id="bare_code"))
    assert "bare_code" in block


@pytest.mark.asyncio
async def test_empty_user_id_returns_empty(db_client):
    rt = _runtime(db_client)
    assert await rt._build_user_identity_block(_Ctx(user_id="")) == ""
