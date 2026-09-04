"""
@file_name: test_working_source_open.py
@author: Bin Liang
@date: 2026-09-04
@description: WorkingSource is an open enum: the core surface (members, value/name, iteration, from_string, pydantic/JSON, is_* predicates) is unchanged and a channel plugin registers its own value.
"""
from __future__ import annotations

import json
import pickle

import pytest
from pydantic import BaseModel

from xyz_agent_context.schema.hook_schema import WorkingSource


def test_core_members_and_enum_surface():
    assert WorkingSource("job") is WorkingSource.JOB and WorkingSource.JOB.value == "job" and WorkingSource.JOB.name == "JOB"
    assert WorkingSource.from_string("CHAT") is WorkingSource.CHAT
    assert WorkingSource.LARK == "lark" and str(WorkingSource.LARK) == "lark" and json.dumps({"s": WorkingSource.LARK}) == '{"s": "lark"}'
    assert "job" in WorkingSource and "nope" not in WorkingSource and WorkingSource.JOB in list(WorkingSource)
    assert {"chat", "job", "a2a", "callback", "skill_study", "message_bus", "manyfold", "lark", "slack", "telegram", "wechat", "narramessenger", "discord"} <= set(WorkingSource.__members__)
    with pytest.raises(ValueError, match="Invalid WorkingSource"):
        WorkingSource("nope")
    assert pickle.loads(pickle.dumps(WorkingSource.JOB)) is WorkingSource.JOB
    assert repr(WorkingSource.CHAT) == "<WorkingSource.CHAT: 'chat'>"


def test_predicates_keep_their_semantics():
    assert WorkingSource.CHAT.is_user_initiated() and not WorkingSource.LARK.is_user_initiated()
    assert WorkingSource.JOB.is_automated() and WorkingSource.LARK.is_automated() and WorkingSource.MANYFOLD.is_automated()
    assert not WorkingSource.CHAT.is_automated() and not WorkingSource.SKILL_STUDY.is_automated()
    assert WorkingSource.CHAT.is_from_human() and WorkingSource.TELEGRAM.is_from_human()
    for bg in (WorkingSource.JOB, WorkingSource.MESSAGE_BUS, WorkingSource.CALLBACK, WorkingSource.SKILL_STUDY):
        assert not bg.is_from_human()


def test_a_plugin_registers_its_channel_source():
    src = WorkingSource.register("acme_chat")
    assert WorkingSource("acme_chat") is src and WorkingSource.register("acme_chat") is src  # idempotent
    assert src.value == "acme_chat" and src.name == "ACME_CHAT" and WorkingSource.is_channel(src)
    assert src.is_from_human() and src.is_automated() and not src.is_user_initiated()
    assert WorkingSource.register("job") is WorkingSource.JOB  # a core name is returned, never redefined
    with pytest.raises(ValueError):
        WorkingSource.register("bad name")


def test_pydantic_fields_accept_core_and_registered_values():
    WorkingSource.register("acme_chat")

    class M(BaseModel):
        source: WorkingSource = WorkingSource.CHAT

    assert M().source is WorkingSource.CHAT
    assert M(source="lark").source is WorkingSource.LARK
    assert M.model_validate({"source": "acme_chat"}).source.name == "ACME_CHAT"
    assert M(source=WorkingSource.JOB).model_dump_json() == '{"source":"job"}'
    with pytest.raises(ValueError):
        M(source="nope")
