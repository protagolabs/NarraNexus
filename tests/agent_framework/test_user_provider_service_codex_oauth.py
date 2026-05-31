"""
@file_name: test_user_provider_service_codex_oauth.py
@date: 2026-05-30
@description: Tests for the codex_oauth branch of
UserProviderService.add_provider — symmetric to the claude_oauth
behaviour, lets users "Add as Provider" from the Settings page so
the Codex OAuth credential becomes assignable to the agent slot.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.user_provider_service import UserProviderService


class _FakeDB:
    """Tiny in-memory user_providers stand-in keyed by provider_id."""

    def __init__(self):
        self.providers: dict[str, dict] = {}  # provider_id -> row
        self.slots: dict[tuple, dict] = {}    # (user_id, slot_name) -> row

    async def get(self, table, filters=None):
        if table == "user_providers":
            filters = filters or {}
            return [
                r for r in self.providers.values()
                if all(r.get(k) == v for k, v in filters.items())
            ]
        if table == "user_slots":
            filters = filters or {}
            return [
                r for r in self.slots.values()
                if all(r.get(k) == v for k, v in filters.items())
            ]
        return []

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        if table == "user_providers":
            self.providers[data["provider_id"]] = dict(data)
        elif table == "user_slots":
            key = (data["user_id"], data["slot_name"])
            self.slots[key] = dict(data)

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        return 0


@pytest.mark.asyncio
async def test_add_codex_oauth_inserts_one_provider_row():
    db = _FakeDB()
    svc = UserProviderService(db)
    config, new_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    assert len(new_ids) == 1
    row = db.providers[new_ids[0]]
    assert row["source"] == "codex_oauth"
    assert row["protocol"] == "openai"
    assert row["auth_type"] == "oauth"
    assert row["api_key"] == ""           # OAuth → no env-key
    assert row["base_url"] == ""           # Codex picks default endpoint
    assert not row["supports_anthropic_server_tools"]  # stored as 0/False
    assert row["name"] == "Codex CLI (OAuth)"


@pytest.mark.asyncio
async def test_add_codex_oauth_rejects_duplicate():
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.add_provider(user_id="u1", card_type="codex_oauth")
    with pytest.raises(ValueError, match="already exists"):
        await svc.add_provider(user_id="u1", card_type="codex_oauth")


@pytest.mark.asyncio
async def test_add_codex_oauth_for_different_user_succeeds():
    """Per-user constraint — user u1's codex_oauth doesn't block u2's."""
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.add_provider(user_id="u1", card_type="codex_oauth")
    config, new_ids = await svc.add_provider(user_id="u2", card_type="codex_oauth")
    assert len(new_ids) == 1


@pytest.mark.asyncio
async def test_add_codex_oauth_protocol_is_openai_not_anthropic():
    """codex_oauth row's protocol is OpenAI (Codex uses OpenAI's
    Responses API surface). This matters for slot eligibility — the
    helper_llm / embedding slots use protocol filtering."""
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    row = db.providers[new_ids[0]]
    assert row["protocol"] == "openai"
    assert row["protocol"] != "anthropic"
