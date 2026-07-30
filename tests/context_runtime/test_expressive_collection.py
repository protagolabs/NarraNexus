"""
@file_name: test_expressive_collection.py
@date: 2026-07-31
@description: Expressive/delivery tool collection — the platform declares
the turn's reply surface to the framework (NexusPower reply contract).

``build_input_for_framework`` collects every active module's
``get_expressive_tools()`` alongside its MCP config, preserving instance
order (priority order: the chat tool lands first and becomes the turn's
default reply tool) and deduplicating. Modules without the method (or
whose lookup crashes) contribute nothing — fail-open, same posture as
``get_disallowed_tools``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.schema import ContextData
from xyz_agent_context.settings import settings

AGENT_ID = "agent_expressive"


class _FakeModule:
    def __init__(self, name: str, expressive: list[str] | None = None, crash: bool = False):
        self.config = SimpleNamespace(name=name, priority=1)
        self._expressive = expressive
        self._crash = crash

    async def get_mcp_config(self):
        return None

    async def get_disallowed_tools(self):
        return []

    async def get_expressive_tools(self):
        if self._crash:
            raise RuntimeError("boom")
        return list(self._expressive or [])

    async def get_turn_context(self, ctx_data) -> str:
        return ""


class _LegacyModule:
    """No get_expressive_tools at all — must not break collection."""

    def __init__(self):
        self.config = SimpleNamespace(name="Legacy", priority=9)

    async def get_mcp_config(self):
        return None

    async def get_disallowed_tools(self):
        return []

    async def get_turn_context(self, ctx_data) -> str:
        return ""


def _inst(module) -> SimpleNamespace:
    return SimpleNamespace(module_class=module.config.name, module=module, instance_id="i")


@pytest.mark.asyncio
async def test_expressive_tools_collected_in_order_deduped_fail_open(monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.agent_id = AGENT_ID
    ctx = ContextData(agent_id=AGENT_ID, user_id=None, input_content="hi")

    chat_tool = "mcp__chat_module__send_message_to_user_directly"
    lark_tool = "mcp__lark_module__lark_cli"
    instances = [
        _inst(_FakeModule("ChatModule", [chat_tool])),
        _inst(_FakeModule("Broken", crash=True)),
        _inst(_LegacyModule()),
        _inst(_FakeModule("LarkModule", [lark_tool, chat_tool])),  # dupe chat
    ]
    _messages, _mcp, _dis, expressive = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="SYSTEM",
        active_instances=instances,
        ctx_data=ctx,
        narrative_list=None,
    )
    # Declaration order preserved (chat first = the turn's default), deduped;
    # the crashing and legacy modules contribute nothing.
    assert expressive == [chat_tool, lark_tool]
