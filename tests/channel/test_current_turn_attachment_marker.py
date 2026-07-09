"""
@file_name: test_current_turn_attachment_marker.py
@date: 2026-07-09
@description: Regressions for current-turn attachment marker injection.

The 2026-07-09 fix ("agent 没看到我上传的图片") ships current-turn
attachment markers via ``ChannelContextBuilderBase.with_current_turn_
attachments`` — historical turns were already covered by ChatModule's
``_synthesize_attachment_markers`` during chat_history assembly, but
the CURRENT turn's attachment was invisible to the agent this turn (it
only surfaced on the NEXT turn via the persisted user row).

These tests lock:

  1. Given a builder with attachments registered, ``build_prompt``
     appends the ``Attachment.synthesize_marker`` output to the
     ``message_body`` slot.
  2. Given no attachments, ``build_prompt`` behaves identically to
     pre-fix behaviour (no marker line).
  3. The marker text carries the absolute path via
     ``resolve_attachment_path`` — this is what tells the agent where
     to Read.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.schema.attachment_schema import (
    Attachment,
    AttachmentCategory,
)


class _MinimalBuilder(ChannelContextBuilderBase):
    """Tiny builder that satisfies the abstract contract with fixed
    metadata — enough to exercise ``build_prompt`` end-to-end without
    a live channel."""

    def __init__(self, message_body: str = "hello agent"):
        self._message_body = message_body

    async def get_message_info(self) -> Dict[str, Any]:
        return {
            "channel_display_name": "TestChannel",
            "channel_key": "testchannel",
            "room_name": "TestRoom",
            "room_id": "!room:test",
            "room_type": "Direct Message",
            "sender_display_name": "Alice",
            "sender_id": "u_alice",
            "timestamp": "1720000000000",
            "my_channel_id": "@agent-x:test",
            "message_body": self._message_body,
            "send_tool_name": "reply",
        }

    async def get_conversation_history(
        self, limit: int
    ) -> List[Dict[str, Any]]:
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        return []


def _make_attachment(file_id: str = "att_1d31e04a") -> Attachment:
    return Attachment(
        file_id=file_id,
        mime_type="image/png",
        original_name="report.png",
        size_bytes=1024,
        category=AttachmentCategory.IMAGE,
        transcript=None,
    )


@pytest.mark.asyncio
async def test_build_prompt_appends_marker_when_attachments_registered(
    monkeypatch,
):
    """Register one attachment via ``with_current_turn_attachments``;
    the rendered prompt MUST contain a Read-tool marker referencing the
    file path. This is the direct regression for the "agent 没看到我上
    传的图片" incident on agent_93461ec945f5 (2026-07-09).

    monkeypatches ``resolve_attachment_path`` so we don't need to
    actually write bytes to disk to exercise the path-substitution
    branch of ``synthesize_marker``.
    """
    from pathlib import Path
    from xyz_agent_context.utils import attachment_storage as storage_mod

    def _fake_resolve(agent_id: str, user_id: str, file_id: str):
        return Path(f"/ws/{user_id}/{agent_id}/user_upload_files/2026-07-09/{file_id}.png")

    monkeypatch.setattr(storage_mod, "resolve_attachment_path", _fake_resolve)

    builder = _MinimalBuilder(message_body="what does this say?")
    att = _make_attachment("att_test001")

    builder.with_current_turn_attachments(
        [att], agent_id="agent_x", owner_user_id="user_owner"
    )

    prompt = await builder.build_prompt(
        ChannelHistoryConfig(load_conversation_history=False)
    )

    assert "what does this say?" in prompt
    # Marker sentinel + path structure the agent's Read tool relies on.
    assert "[User uploaded image:" in prompt
    assert "att_test001" in prompt
    assert "/ws/user_owner/agent_x/" in prompt  # ownership routing correct
    assert "use Read tool to view]" in prompt


@pytest.mark.asyncio
async def test_build_prompt_no_attachments_leaves_message_body_untouched():
    """No ``with_current_turn_attachments`` call → no marker,
    message_body is exactly what was returned by ``get_message_info``."""
    builder = _MinimalBuilder(message_body="just some text")

    prompt = await builder.build_prompt(
        ChannelHistoryConfig(load_conversation_history=False)
    )

    assert "just some text" in prompt
    assert "[User uploaded" not in prompt
    assert "use Read tool to view" not in prompt


@pytest.mark.asyncio
async def test_build_prompt_appends_multiple_markers_one_per_attachment(
    monkeypatch,
):
    """The user may upload several files in one turn; each Attachment
    gets its own marker line and they're joined with newlines."""
    from pathlib import Path
    from xyz_agent_context.utils import attachment_storage as storage_mod

    monkeypatch.setattr(
        storage_mod,
        "resolve_attachment_path",
        lambda agent_id, user_id, file_id: Path(f"/ws/{file_id}.png"),
    )

    builder = _MinimalBuilder(message_body="look at these")
    atts = [
        _make_attachment("att_first"),
        _make_attachment("att_second"),
    ]

    builder.with_current_turn_attachments(
        atts, agent_id="agent_x", owner_user_id="user_owner"
    )

    prompt = await builder.build_prompt(
        ChannelHistoryConfig(load_conversation_history=False)
    )

    assert "att_first" in prompt
    assert "att_second" in prompt
    # Both markers present + separated by newline (visible in the same
    # ``## Current Message`` section, not scattered across the template).
    first_idx = prompt.find("att_first")
    second_idx = prompt.find("att_second")
    assert first_idx > 0 and second_idx > first_idx


@pytest.mark.asyncio
async def test_with_current_turn_attachments_is_chainable():
    """API sugar: ``with_current_turn_attachments`` returns ``self`` so
    triggers can write it inline before ``build_prompt``."""
    builder = _MinimalBuilder()
    returned = builder.with_current_turn_attachments(
        [_make_attachment()], agent_id="a", owner_user_id="u"
    )
    assert returned is builder


@pytest.mark.asyncio
async def test_empty_attachments_list_is_treated_as_no_attachments():
    """An empty list (rather than ``None``) MUST behave the same as no
    call at all — no marker, no exception."""
    builder = _MinimalBuilder(message_body="hi")
    builder.with_current_turn_attachments(
        [], agent_id="a", owner_user_id="u"
    )
    prompt = await builder.build_prompt(
        ChannelHistoryConfig(load_conversation_history=False)
    )
    assert "hi" in prompt
    assert "[User uploaded" not in prompt
