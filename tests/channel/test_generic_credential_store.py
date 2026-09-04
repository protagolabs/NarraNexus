"""
@file_name: test_generic_credential_store.py
@author: Bin Liang
@date: 2026-09-04
@description: channel_credentials + GenericCredentialStore: values split by the channel schema (secrets encrypted, identity public, external id unique), CRUD/list, and the mirror that copies every bespoke-manager write into the generic table.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from xyz_agent_context.channel import credential_codec
from xyz_agent_context.channel.credential_mirror import backfill, install_manager_mirrors, sync_from_manager
from xyz_agent_context.channel.credential_store import TABLE, GenericCredentialStore, UnknownChannel, missing_required, split_values
from xyz_agent_context.module.contributions import register_all

PLUGIN = ChannelDescriptor(
    name="acme_chat",
    display_name="Acme Chat",
    transport="webhook",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("bot_token", "secret", required=True),
            CredentialField("bot_id", "string", required=False),
            CredentialField("workspace", "string", required=True),
        ),
        supports_test=False,
        external_id_field="bot_id",
    ),
    has_bind=True,
    has_test=False,
)


# A bespoke manager the mirror wraps (stands in for the six builtin ones).
@dataclass
class FakeCred:
    agent_id: str
    bot_token: str
    bot_id: str
    enabled: int = 1

    def to_raw_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "bot_token": self.bot_token, "bot_id": self.bot_id, "enabled": self.enabled}


class FakeManager:
    rows: dict[str, FakeCred] = {}

    def __init__(self, db):
        self._db = db

    async def bind(self, agent_id: str, bot_token: str) -> dict:
        self.rows[agent_id] = FakeCred(agent_id, bot_token, bot_id=f"bot-{agent_id}")
        return {"success": True}

    async def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        cred = self.rows.get(agent_id)
        if cred is None:
            return False
        cred.enabled = 1 if enabled else 0
        return True

    async def unbind(self, agent_id: str) -> bool:
        return self.rows.pop(agent_id, None) is not None

    async def get(self, agent_id: str) -> Optional[FakeCred]:
        return self.rows.get(agent_id)

    async def list_active(self) -> list[FakeCred]:
        return [c for c in self.rows.values() if c.enabled]


MANAGED = ChannelDescriptor(
    name="fake_managed",
    display_name="Fake Managed",
    credential_schema=CredentialSchema(fields=(CredentialField("bot_token", "secret"), CredentialField("bot_id")), external_id_field="bot_id"),
    credential_manager_ref=f"{__name__}:FakeManager",
    credential_read_method="get",
)


@pytest.fixture(autouse=True)
def _key_dir(tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")
    yield
    credential_codec.use_key_dir(None)


@pytest.fixture
def regs() -> Registries:
    r = Registries()
    register_all(r)
    reg = r.registry_for("ingress.channels")
    reg.register_contribution(Contribution("acme_chat", lambda: PLUGIN), owner="acme.chat")
    reg.register_contribution(Contribution("fake_managed", lambda: MANAGED), owner="acme.managed")
    FakeManager.rows = {}
    return r


def test_split_by_schema_and_secret_heuristics():
    public, secret, external = split_values(PLUGIN, {"bot_token": "t", "bot_id": "b1", "workspace": "w", "refresh_token": "r", "agent_id": "ignored"})
    assert public == {"bot_id": "b1", "workspace": "w"} and secret == {"bot_token": "t", "refresh_token": "r"} and external == "b1"
    assert missing_required(PLUGIN, {"bot_token": "t"}) == ("workspace",)
    assert missing_required(PLUGIN, {"bot_token": "t", "workspace": "w"}) == ()


def test_codec_roundtrip_is_encrypted():
    blob = credential_codec.encode_secrets({"bot_token": "top-secret"})
    assert "top-secret" not in blob and credential_codec.decode_secrets(blob) == {"bot_token": "top-secret"}
    assert credential_codec.encode_secrets({}) == "" and credential_codec.decode_secrets("") == {}


@pytest.mark.asyncio
async def test_store_crud_and_secret_at_rest(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    rec = await store.upsert("acme_chat", "a1", {"bot_token": "tok", "bot_id": "b1", "workspace": "w"})
    assert rec.to_public_dict() == {"channel": "acme_chat", "agent_id": "a1", "enabled": True, "external_id": "b1", "bot_id": "b1", "workspace": "w"}
    assert rec.to_raw_dict()["bot_token"] == "tok"
    row = await db_client.get_one(TABLE, {"channel": "acme_chat", "agent_id": "a1"})
    assert "tok" not in row["secret_json"] and "tok" not in row["public_json"]
    assert (await store.get_public("acme_chat", "a1"))["workspace"] == "w"
    assert await store.set_enabled("acme_chat", "a1", False) and not (await store.get("acme_chat", "a1")).enabled
    assert [r.agent_id for r in await store.list_active("acme_chat")] == []
    assert await store.update("acme_chat", "a1", {"workspace": "w2"})
    assert (await store.get("acme_chat", "a1")).public["workspace"] == "w2" and (await store.get("acme_chat", "a1")).secret["bot_token"] == "tok"
    assert await store.unbind("acme_chat", "a1") and not await store.unbind("acme_chat", "a1")
    with pytest.raises(UnknownChannel):
        await store.upsert("nope", "a1", {})


@pytest.mark.asyncio
async def test_external_id_is_unique_per_channel(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await store.upsert("acme_chat", "a1", {"bot_token": "t", "bot_id": "same", "workspace": "w"})
    with pytest.raises(Exception):
        await store.upsert("acme_chat", "a2", {"bot_token": "t", "bot_id": "same", "workspace": "w"})


@pytest.mark.asyncio
async def test_manager_writes_are_mirrored(db_client, regs):
    wired = install_manager_mirrors(regs)
    assert "fake_managed" in wired and "lark" in wired
    install_manager_mirrors(regs)  # idempotent: no double wrapping
    mgr = FakeManager(db_client)
    await mgr.bind("a1", "tok")
    store = GenericCredentialStore(db_client, regs)
    rec = await store.get("fake_managed", "a1")
    assert rec is not None and rec.external_id == "bot-a1" and rec.secret == {"bot_token": "tok"} and rec.enabled
    await mgr.set_enabled("a1", False)
    assert not (await store.get("fake_managed", "a1")).enabled
    await mgr.unbind("a1")
    assert await store.get("fake_managed", "a1") is None


@pytest.mark.asyncio
async def test_backfill_copies_active_rows(db_client, regs):
    FakeManager.rows = {"a1": FakeCred("a1", "t1", "b1"), "a2": FakeCred("a2", "t2", "b2", enabled=0)}
    counts = await backfill(db_client, registries=regs)
    assert counts["fake_managed"] == 1
    store = GenericCredentialStore(db_client, regs)
    assert (await store.get("fake_managed", "a1")).secret["bot_token"] == "t1" and await store.get("fake_managed", "a2") is None
    assert await sync_from_manager(db_client, "fake_managed", "a2", registries=regs) is not None  # an explicit sync copies the inactive one too


def test_migration_registered():
    from backend.migrations import REGISTRY

    assert REGISTRY[-1].id == "0004_channel_credentials_backfill"
