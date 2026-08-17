"""
@file_name: test_inbox_recorder_db.py
@author:
@date: 2026-08-17
@description: The recorder against a real database — repeat-safety and the
guards that survived the move off the bus tables.

Replaces `test_channel_inbox_writer.py`. Most of what that file pinned is now
in `test_inbox_recorder.py` (fake-db, faster): the thread is created once, a
placeholder name is refreshed, a silent turn writes one row, thread ids carry
their family prefix. Two guards had no home in that file and are relocated
here rather than dropped:

  * repeat-write safety — the old writer's "get, then insert only when
    missing" shape is easy to lose in a refactor and the symptom (a duplicated
    thread per inbound message) is only visible against a real DB;
  * the constructor's rejection of an empty source.

Deliberately NOT carried over: assertions about `bus_agent_registry`,
`bus_channels` and `bus_channel_members` rows. Those rows are the thing this
change removes — `test_inbox_recorder.py::test_the_recorder_never_writes_a_bus_table`
is now the guard that they stay gone.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.channel.inbox_recorder import InboxRecorder, im_thread_id


async def _record(db, *, original="hello", response="hi back",
                  channel="slack", brand="Slack", chat_id="C_room",
                  agent_id="agent_a", sender_id="U_alice", sender_name="Alice"):
    await InboxRecorder(channel, brand).record_turn(
        db=db,
        thread_id=im_thread_id(channel, chat_id),
        owner_user_id="usr_owner",
        agent_id=agent_id,
        counterpart_id=sender_id,
        counterpart_name=sender_name,
        inbound_text=original,
        outbound_text=response,
    )


@pytest.mark.asyncio
async def test_recording_twice_reuses_the_thread_and_appends_messages(db_client):
    """One thread, four messages.

    The old writer created its thread-equivalent with "read first, insert only
    when missing", and a refactor that loses that shape duplicates a row per
    inbound message — invisible in a fake-db test, obvious here.
    """
    await _record(db_client)
    await _record(db_client)

    thread_id = im_thread_id("slack", "C_room")
    threads = await db_client.get("inbox_threads", {"thread_id": thread_id})
    assert len(threads) == 1, f"thread duplicated: {threads!r}"

    msgs = await db_client.get("inbox_thread_messages", {"thread_id": thread_id})
    assert len(msgs) == 4, "each turn appends one inbound and one outbound row"


@pytest.mark.asyncio
async def test_the_thread_row_carries_the_owner_and_the_brand_title(db_client):
    await _record(db_client)

    thread = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", "C_room")}
    )
    assert thread["owner_user_id"] == "usr_owner"
    assert thread["agent_id"] == "agent_a"
    assert thread["source"] == "slack"
    assert thread["title"] == "Slack: Alice"


@pytest.mark.asyncio
async def test_two_channels_with_the_same_chat_id_do_not_collide(db_client):
    """Some platforms emit short numeric chat ids; an unprefixed thread id
    would merge a Slack conversation into a Telegram one."""
    await _record(db_client, channel="slack", brand="Slack", chat_id="123")
    await _record(db_client, channel="telegram", brand="Telegram", chat_id="123")

    slack = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", "123")}
    )
    tg = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("telegram", "123")}
    )
    assert slack["title"] == "Slack: Alice"
    assert tg["title"] == "Telegram: Alice"


def test_the_recorder_rejects_an_empty_source():
    """The source is identity — it names the thread and matches the registry.

    An empty brand is allowed and falls back to a title-cased source: the brand
    is a display label, and refusing to record a turn because nobody supplied a
    pretty name would lose the conversation over cosmetics. The old writer
    rejected both; only the identity half is worth a hard failure.
    """
    with pytest.raises(ValueError):
        InboxRecorder("", "Slack")

    assert InboxRecorder("slack", "")._brand == "Slack"
