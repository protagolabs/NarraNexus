"""
@file_name: test_expressive_declaration.py
@date: 2026-07-31
@description: ChatModule declares the owner-chat delivery tool (NexusPower
reply contract). Chat sits first in module priority order, so this
declaration becomes the turn's default reply tool — the name the
framework's constitution renders as its example.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule


@pytest.mark.asyncio
async def test_chat_module_declares_the_owner_chat_reply_tool():
    module = ChatModule(agent_id="agent_a", user_id=None, database_client=MagicMock())
    assert await module.get_expressive_tools() == [
        "mcp__chat_module__send_message_to_user_directly"
    ]
