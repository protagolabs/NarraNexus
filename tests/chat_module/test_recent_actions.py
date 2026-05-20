"""
@file_name: test_recent_actions.py
@author: Bin Liang
@date: 2026-05-20
@description: Recent-actions track (Fix #2 P2).

_load_recent_actions collects the latest background 'activity' records (the
centered small-text items in the chat UI — turns where the agent did work
WITHOUT replying to the user). They are NOT in the chat timeline; surfaced as a
compact list, latest RECENT_ACTIONS_MAX, each with its event_id (for
view_event drill-down) and narrative_id, sorted oldest -> newest. Non-activity
rows are excluded.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id="a_act", user_id="u_act", database_client=db_client, instance_id="chat_cur"
    )


def _msg(role, content, ts, event_id, activity=False, ws="chat"):
    meta = {"timestamp": ts, "event_id": event_id, "working_source": ws}
    if activity:
        meta["message_type"] = "activity"
    return {"role": role, "content": content, "meta_data": meta}


async def test_recent_actions_collects_activity_rows_only_latest_first(chat_module):
    fake_instances = [
        type("FI", (), {"instance_id": "chat_cur", "linked_narrative_ids": ["nar_cur"]})(),
        type("FI", (), {"instance_id": "chat_other", "linked_narrative_ids": ["nar_other"]})(),
    ]
    fake_memories = {
        "chat_cur": {"messages": [
            _msg("user", "hi", _ts(50), "evt_chat1"),                      # not activity
            _msg("assistant", "Executed a background job", _ts(40), "evt_job1", activity=True, ws="job"),
        ]},
        "chat_other": {"messages": [
            _msg("assistant", "Background activity (message_bus)", _ts(10), "evt_bus1", activity=True, ws="message_bus"),
            _msg("assistant", "reply to user", _ts(5), "evt_chat2"),       # not activity
        ]},
    }
    chat_module.event_memory_module.search_instance_json_format_memory = AsyncMock(
        side_effect=lambda module_name, inst_id: fake_memories.get(inst_id)
    )
    with patch(
        "xyz_agent_context.utils.db_factory.get_db_client",
        new=AsyncMock(return_value=MagicMock(get_by_ids=AsyncMock(return_value=[]))),
    ), patch(
        "xyz_agent_context.repository.InstanceRepository.get_chat_instances_by_user",
        new=AsyncMock(return_value=fake_instances),
    ):
        actions = await chat_module._load_recent_actions()

    # Only the 2 activity rows, oldest -> newest, with event_id + narrative_id.
    assert [a["event_id"] for a in actions] == ["evt_job1", "evt_bus1"]
    assert [a["working_source"] for a in actions] == ["job", "message_bus"]
    assert actions[0]["narrative_id"] == "nar_cur"
    assert actions[1]["narrative_id"] == "nar_other"


async def test_recent_actions_caps_at_max(chat_module):
    cap = ChatModule.RECENT_ACTIONS_MAX
    msgs = [_msg("assistant", f"act {i}", _ts(cap + 5 - i), f"evt_{i}", activity=True, ws="job") for i in range(cap + 5)]
    fake_instances = [type("FI", (), {"instance_id": "chat_cur", "linked_narrative_ids": ["nar_cur"]})()]
    chat_module.event_memory_module.search_instance_json_format_memory = AsyncMock(
        return_value={"messages": msgs}
    )
    with patch(
        "xyz_agent_context.utils.db_factory.get_db_client",
        new=AsyncMock(return_value=MagicMock(get_by_ids=AsyncMock(return_value=[]))),
    ), patch(
        "xyz_agent_context.repository.InstanceRepository.get_chat_instances_by_user",
        new=AsyncMock(return_value=fake_instances),
    ):
        actions = await chat_module._load_recent_actions()

    assert len(actions) == cap
