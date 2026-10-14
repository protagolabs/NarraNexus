"""
@file_name: test_credential_reset_on_disable.py
@date: 2026-10-14
@description: ``CredentialSchema.reset_on_disable`` — per-channel stream state
that must not survive a deliberate switch-off.

A binding that is turned off and later back on must start its transport fresh:
a resumed Matrix /sync cursor would deliver everything sent while it was off
and the agent would answer days-old messages. ``GenericCredentialStore.set_enabled``
is the single choke point every disable path funnels through (the set-active
route, ``disable_credential``, the per-channel managers), so the declared
fields are cleared there — and only there, never on enable and never per sync.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec
from narranexus.platform.channel.credential_store import GenericCredentialStore


def _descriptor(name: str, reset: tuple[str, ...]) -> ChannelDescriptor:
    return ChannelDescriptor(
        name=name,
        display_name=name,
        transport="webhook",
        credential_schema=CredentialSchema(
            fields=(
                CredentialField("bot_token", "secret", required=True),
                CredentialField("bot_id", "string", required=False),
            ),
            supports_test=False,
            external_id_field="bot_id",
            reset_on_disable=reset,
        ),
        has_bind=True,
        has_test=False,
    )


@pytest.fixture(autouse=True)
def _key_dir(tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")
    yield
    credential_codec.use_key_dir(None)


@pytest.fixture
def regs() -> Registries:
    r = Registries()
    load_builtins(r, "backend")
    reg = r.registry_for("ingress.channels")
    reg.register_contribution(Contribution("cursor_chat", lambda: _descriptor("cursor_chat", ("cursor",))), owner="t.a")
    reg.register_contribution(Contribution("plain_chat", lambda: _descriptor("plain_chat", ())), owner="t.b")
    return r


async def _bind(store: GenericCredentialStore, channel: str) -> None:
    await store.upsert(channel, "a1", {"bot_token": "t", "bot_id": "b1", "cursor": "s42", "note": "keep"})


@pytest.mark.asyncio
async def test_disable_clears_the_declared_fields_only(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat")
    assert await store.set_enabled("cursor_chat", "a1", False, reason="owner")
    rec = await store.get("cursor_chat", "a1")
    assert rec.public["cursor"] == ""
    assert rec.public["note"] == "keep"
    assert rec.secret["bot_token"] == "t"
    assert rec.public["disabled_reason"] == "owner"


@pytest.mark.asyncio
async def test_enable_does_not_touch_the_declared_fields(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat")
    await store.patch("cursor_chat", "a1", {"cursor": "s99"})
    assert await store.set_enabled("cursor_chat", "a1", True)
    assert (await store.get("cursor_chat", "a1")).public["cursor"] == "s99"


@pytest.mark.asyncio
async def test_a_channel_without_the_declaration_keeps_everything(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "plain_chat")
    assert await store.set_enabled("plain_chat", "a1", False)
    assert (await store.get("plain_chat", "a1")).public["cursor"] == "s42"


def test_narramessenger_declares_its_sync_cursor_as_reset_on_disable():
    from narranexus.platform.channel.credential_store import descriptor_for

    assert "matrix_since_token" in descriptor_for("narramessenger").credential_schema.reset_on_disable
