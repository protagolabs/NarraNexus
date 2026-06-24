"""
@file_name: test_team_room_chat_routing.py
@author: NetMind.AI
@date: 2026-06-24
@description: step_1 keys a team-room narrative's ChatModule instance under the
room-scoped pseudo-user (not the owner), so the owner's 1:1 simple-chat-history
never surfaces group chat. Normal narratives keep using the run's user_id.
"""
from __future__ import annotations

from types import SimpleNamespace

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_1_select_narrative import (
    _chat_user_id_for_narrative,
)


def test_normal_narrative_uses_run_user_id():
    narrative = SimpleNamespace(is_special="other", env_variables={})
    assert _chat_user_id_for_narrative("user_owner", narrative) == "user_owner"


def test_default_narrative_uses_run_user_id():
    narrative = SimpleNamespace(is_special="default", env_variables={})
    assert _chat_user_id_for_narrative("user_owner", narrative) == "user_owner"


def test_team_room_narrative_uses_room_user_id():
    narrative = SimpleNamespace(
        is_special="team_room",
        env_variables={"bus_channel_id": "chan_x", "room_user_id": "room_chan_x"},
    )
    assert _chat_user_id_for_narrative("user_owner", narrative) == "room_chan_x"


def test_team_room_without_room_user_falls_back_to_owner():
    # Defensive: a malformed team_room narrative missing room_user_id must not
    # crash — fall back to the run user_id rather than route nowhere.
    narrative = SimpleNamespace(is_special="team_room", env_variables={})
    assert _chat_user_id_for_narrative("user_owner", narrative) == "user_owner"
