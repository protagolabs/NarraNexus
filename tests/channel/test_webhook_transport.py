"""
@file_name: test_webhook_transport.py
@author: Bin Liang
@date: 2026-09-04
@description: The webhook transport: inbox push/pull/claim order/purge, request verification (token / HMAC / negatives), and a webhook trigger whose connect() drains the inbox for its generic-store credentials.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path
from typing import Any, Optional

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from xyz_agent_context.channel import credential_codec
from xyz_agent_context.channel.credential_store import GenericCredentialStore
from xyz_agent_context.channel.webhook_inbox import WebhookInbox
from xyz_agent_context.channel.webhook_transport import WebhookChannelTriggerBase, new_webhook_secret, verify_webhook
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ParsedMessage

DESC = ChannelDescriptor(name="wh_demo", display_name="WH", transport="webhook", credential_schema=CredentialSchema(fields=(CredentialField("api_token", "secret"),), supports_test=False))


class DemoTrigger(WebhookChannelTriggerBase):
    channel_name = "wh_demo"
    brand_display = "WH"
    working_source = WorkingSource.register("wh_demo")
    WEBHOOK_POLL_SECONDS = 0.01

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        return ParsedMessage(message_id=str(raw["id"]), chat_id="c", sender_id="s", content=str(raw.get("text", ""))) if raw.get("id") else None

    async def is_echo(self, message, credential) -> bool:
        return False

    async def resolve_sender_name(self, sender_id, credential) -> str:
        return sender_id

    def create_context_builder(self, message, credential, agent_id):  # pragma: no cover - not exercised here
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _keys(tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")
    yield
    credential_codec.use_key_dir(None)


@pytest.fixture
def regs():
    r = Registries()
    r.registry_for("ingress.channels").register_contribution(Contribution("wh_demo", lambda: DESC), owner="acme.wh")
    return r


@pytest.mark.asyncio
async def test_inbox_claims_in_order_and_purges(db_client):
    inbox = WebhookInbox(db_client)
    for i in range(3):
        await inbox.push("wh_demo", "a1", {"id": i})
    await inbox.push("wh_demo", "a2", {"id": 99})
    assert await inbox.pending("wh_demo", "a1") == 3
    first = await inbox.pull("wh_demo", "a1", limit=2)
    assert [e.payload["id"] for e in first] == [0, 1]
    rest = await inbox.pull("wh_demo", "a1")
    assert [e.payload["id"] for e in rest] == [2] and await inbox.pull("wh_demo", "a1") == []
    assert await inbox.pending("wh_demo", "a2") == 1  # other agents untouched
    assert await inbox.purge_claimed("wh_demo", "a1") == 3


def test_verify_webhook_token_and_hmac():
    secret = new_webhook_secret()
    body = b'{"id": 1}'
    assert verify_webhook(secret, body, {"X-Webhook-Token": secret})
    assert verify_webhook(secret, body, {}, query_token=secret)
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook(secret, body, {"x-webhook-signature": f"sha256={sig}"})
    assert not verify_webhook(secret, body + b" ", {"x-webhook-signature": f"sha256={sig}"})  # body tampered
    assert not verify_webhook(secret, body, {"X-Webhook-Token": "nope"})
    assert not verify_webhook("", body, {"X-Webhook-Token": ""})
    assert not verify_webhook(secret, body, {})


@pytest.mark.asyncio
async def test_webhook_trigger_drains_the_inbox_for_its_generic_credentials(db_client, regs, monkeypatch):
    monkeypatch.setattr("narranexus.kernel.plugins.registries.KERNEL_REGISTRIES", regs)
    store = GenericCredentialStore(db_client, regs)
    await store.upsert("wh_demo", "a1", {"api_token": "t", "webhook_secret": "s"})
    await store.upsert("wh_demo", "a2", {"api_token": "t"}, enabled=False)
    trigger = DemoTrigger(max_workers=1)
    trigger._db = db_client
    trigger.running = True
    creds = await trigger.load_active_credentials()
    assert [c.agent_id for c in creds] == ["a1"] and creds[0].app_id == "a1"
    inbox = WebhookInbox(db_client)
    await inbox.push("wh_demo", "a1", {"id": 7, "text": "hi"})
    gen = trigger.connect(creds[0])
    raw = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert raw["id"] == 7 and raw["_webhook_event_id"] >= 1
    assert trigger.parse_event(raw).content == "hi"
    trigger.running = False
    await gen.aclose()  # type: ignore[attr-defined] — the async generator object has aclose
