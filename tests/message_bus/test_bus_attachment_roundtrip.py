"""
@file_name: test_bus_attachment_roundtrip.py
@date: 2026-07-20
@description: Attachments survive a bus send→read round-trip (JSON column) and
surface as Read-tool markers in both prompt builders (DM/owner-relay + team).
"""

from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage

ATTS = [
    {
        "file_id": "att_1234abcd",
        "original_name": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12,
        "category": "document",
        "rel_path": "user_a/_shared/bus_files/2026-07-20/att_1234abcd.pdf",
    }
]


async def _seed(db, agent_id, owner):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": owner})


@pytest.mark.asyncio
async def test_send_read_roundtrip_and_msg_type(db_client):
    await _seed(db_client, "agent_a", "user_a")
    await _seed(db_client, "agent_a2", "user_a")
    bus = LocalMessageBus(db_client._backend)

    msg_id = await bus.send_to_agent(
        from_agent="agent_a",
        to_agent="agent_a2",
        content="here is the report",
        attachments=ATTS,
    )
    assert msg_id

    # Fetch via the DM channel the send auto-created.
    channel = await db_client.get_one("bus_channels", {"created_by": "agent_a"})
    msgs = await bus.get_messages(channel["channel_id"])
    assert len(msgs) == 1
    got = msgs[0]
    assert got.attachments == ATTS
    assert got.msg_type == "multimodal"  # auto-tagged when files present


@pytest.mark.asyncio
async def test_text_only_stays_text(db_client):
    await _seed(db_client, "agent_a", "user_a")
    await _seed(db_client, "agent_a2", "user_a")
    bus = LocalMessageBus(db_client._backend)
    await bus.send_to_agent(from_agent="agent_a", to_agent="agent_a2", content="ping")
    channel = await db_client.get_one("bus_channels", {"created_by": "agent_a"})
    msgs = await bus.get_messages(channel["channel_id"])
    assert msgs[0].msg_type == "text"
    assert msgs[0].attachments is None


def _msg(**kw):
    base = dict(
        message_id="msg_1",
        channel_id="ch_1",
        from_agent="agent_a",
        content="see attached",
        msg_type="multimodal",
        mentions=None,
        attachments=ATTS,
        created_at="2026-07-20T00:00:00Z",
    )
    base.update(kw)
    return BusMessage(**base)


def test_owner_relay_prompt_injects_marker():
    trig = MessageBusTrigger(bus=None)
    prompt = trig._build_prompt([_msg()], owner_user_id="user_a", owner_name="Bin")
    assert "use Read tool" in prompt
    assert "report.pdf" in prompt


def test_team_prompt_injects_marker_and_shared_folder():
    trig = MessageBusTrigger(bus=None)
    member_map = {"agent_a": "Alice", "agent_b": "Bob"}
    prompt = trig._build_team_prompt(
        "agent_b",
        [_msg()],
        member_map,
        owner_user_id="user_a",
        team_id="team_42",
    )
    assert "use Read tool" in prompt  # attachment marker
    assert "Team shared folder" in prompt  # collaboration hint
    assert "teams/team_42" in prompt


def test_team_prompt_allows_read_forbids_send():
    # The reply-only rule must never blanket-forbid tools — a shared file (as a
    # marker OR a path pasted in text) is answered by Read-ing it. The blocker
    # was the old "Do NOT use any tools" rule; agents refused to open the file.
    trig = MessageBusTrigger(bus=None)
    for atts, mtype in [(ATTS, "multimodal"), (None, "text")]:
        prompt = trig._build_team_prompt(
            "agent_b",
            [_msg(attachments=atts, msg_type=mtype, content="see the path")],
            {"agent_a": "Alice", "agent_b": "Bob"},
        )
        assert "built-in Read tool" in prompt          # Read always allowed
        assert "Do NOT use any tools" not in prompt      # no blanket ban
        assert "send/bus" in prompt                      # but send/bus still forbidden
