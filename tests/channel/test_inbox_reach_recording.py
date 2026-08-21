"""
@file_name: test_inbox_reach_recording.py
@author:
@date: 2026-08-21
@description: Reachability is recorded AUTOMATICALLY on an inbound channel turn.

Before this, `contact_info.channels` was populated only if the model remembered
to call `extract_entity_info` (channel prompt instruction #5) — so the address
book was effectively empty, and an agent on another surface had no way to learn
it could reach a contact on Lark/Slack/etc.

`InboxRecorder.record_turn` fires on every inbound channel turn and knows the
channel, the agent, the counterpart, and the conversation id. It now writes
"agent reaches counterpart on <channel> via <chat_id>" onto the counterpart's
social entity — the single home for reach (no parallel per-surface rosters).
Best-effort: it must never break the inbox write.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.channel.channel_contact_utils import get_room_id
from xyz_agent_context.channel.inbox_recorder import InboxRecorder, im_thread_id
from xyz_agent_context.repository.social_network_repository import SocialNetworkRepository

AGENT, COUNTERPART, CHAT = "agent_a", "U_alice", "C_room7"
INSTANCE = "social_test0001"


@pytest.fixture(autouse=True)
def _direct_store(db_client, monkeypatch):
    """DirectStore (no backend URL) over the in-memory db."""
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)

    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _get_db
    )


async def _seed_social_instance(db):
    await db.insert(
        "module_instances",
        {"instance_id": INSTANCE, "module_class": "SocialNetworkModule",
         "agent_id": AGENT, "is_public": 1},
    )


async def _record(db, *, channel="slack", chat_id=CHAT, counterpart=COUNTERPART):
    await InboxRecorder(channel, channel.title()).record_turn(
        db=db,
        thread_id=im_thread_id(channel, AGENT, chat_id),
        owner_user_id="usr_owner",
        agent_id=AGENT,
        counterpart_id=counterpart,
        counterpart_name="Alice",
        inbound_text="hi",
        outbound_text="hello",
        chat_id=chat_id,
    )


@pytest.mark.asyncio
async def test_an_inbound_turn_records_how_to_reach_the_sender(db_client):
    """The whole point: after Alice writes on Slack, the agent's social graph
    knows it can reach Alice on Slack in conversation C_room7."""
    await _seed_social_instance(db_client)

    await _record(db_client)

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity is not None, "no social entity was written for the sender"
    assert get_room_id(entity.contact_info, "slack", AGENT) == CHAT


@pytest.mark.asyncio
async def test_reach_is_not_recorded_without_a_conversation_id(db_client):
    """No chat_id → nothing to point at → no reach write (and no crash)."""
    await _seed_social_instance(db_client)

    await _record(db_client, chat_id="")

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity is None or not entity.contact_info.get("channels")


@pytest.mark.asyncio
async def test_reach_failure_never_breaks_the_inbox_write(db_client, monkeypatch):
    """Best-effort: if the reach write RAISES, the inbox turn is still recorded
    and no exception escapes `record_turn`.

    The store's own no-instance path returns an in-band error dict rather than
    raising, so an absent instance does NOT exercise the `try/except`. We force a
    real raise (an ImportError-shaped failure of the lazy `get_agent_data_store`
    import path is the realistic future regression) and prove the inbox row and
    the caller survive it. Delete `_record_reach`'s `try/except` → this goes red.
    """
    await _seed_social_instance(db_client)

    def _boom(*a, **k):
        raise RuntimeError("store exploded")

    # Patched at its source: `_record_reach` does `from ...data_access import
    # get_agent_data_store` at call time, so it re-reads this symbol.
    monkeypatch.setattr(
        "xyz_agent_context.module.data_access.get_agent_data_store", _boom
    )

    await _record(db_client)  # must NOT raise

    thread = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", AGENT, CHAT)}
    )
    assert thread is not None, "the inbox write was lost to a reach failure"
