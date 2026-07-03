"""
@file_name: test_wechat_send_delivery.py
@author: Bin Liang
@date: 2026-07-03
@description: Delivery-path hardening for wechat_send after the 2026-07-03
              silent-drop incident.

The iLink gateway returns ``ret=0`` ("ok") even for messages it never
delivers. Two confirmed drop classes on dev (agent_0ed73ae78099):
  - replies containing non-BMP characters (4-byte UTF-8, e.g. 🍉) — accepted,
    never delivered; the BMP-only reply in the same session delivered fine;
  - sends with a fabricated context_token — also "ok".

Hardening under test here:
  1. ``sanitize_bmp`` strips non-BMP chars before send (delivered-without-
     the-emoji beats silently-dropped) and ``send_message`` applies it.
  2. The reply prompt warns the agent off emoji.
  3. Send failures are no longer swallowed silently — ``send_message`` logs
     per-chunk failures.
"""

import httpx
import pytest

from xyz_agent_context.module.wechat_module.wechat_context_builder import (
    WeChatContextBuilder,
)
from xyz_agent_context.module.wechat_module._wechat_credential_manager import (
    WeChatCredential,
)
from xyz_agent_context.module.wechat_module.wechat_sdk_client import (
    WeChatSDKClient,
    sanitize_bmp,
)
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def test_sanitize_bmp_strips_non_bmp_keeps_bmp():
    # 🍉 (U+1F349) is non-BMP; ～！“” are BMP and must survive.
    assert sanitize_bmp("是手滑了吧 🍉") == "是手滑了吧 "
    assert sanitize_bmp("大西瓜你好！我是小冰～") == "大西瓜你好！我是小冰～"
    assert sanitize_bmp("") == ""
    # BMP emoji (U+263A) survives; only astral-plane chars are dropped.
    assert sanitize_bmp("hi ☺ there 🎉") == "hi ☺ there "


def _client_capturing(bodies: list) -> WeChatSDKClient:
    def handler(req: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"ret": 0})

    c = WeChatSDKClient("tok", "https://gw.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_send_message_strips_non_bmp_from_wire_payload():
    bodies: list = []
    c = _client_capturing(bodies)
    ok = await c.send_message("u@im.wechat", "tokctx", "手滑了吧 🍉!")
    await c.aclose()
    assert ok is True
    assert len(bodies) == 1
    sent_text = bodies[0]["msg"]["item_list"][0]["text_item"]["text"]
    assert "🍉" not in sent_text
    assert sent_text == "手滑了吧 !"


@pytest.mark.asyncio
async def test_send_message_all_non_bmp_returns_false_without_posting():
    """A reply that sanitises to empty must not fire an empty-text send."""
    bodies: list = []
    c = _client_capturing(bodies)
    ok = await c.send_message("u@im.wechat", "tokctx", "🍉🎉")
    await c.aclose()
    assert ok is False
    assert bodies == []


@pytest.mark.asyncio
async def test_send_message_logs_chunk_failure():
    from loguru import logger

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -1})

    c = WeChatSDKClient("tok", "https://gw.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(str(m)), level="WARNING")
    import asyncio as _asyncio

    async def _noop(*_a, **_k):
        return None

    orig_sleep = _asyncio.sleep
    _asyncio.sleep = _noop
    try:
        ok = await c.send_message("u@im.wechat", "tokctx", "hello")
    finally:
        _asyncio.sleep = orig_sleep
        logger.remove(sink_id)
        await c.aclose()
    assert ok is False
    assert any("wechat send" in line.lower() for line in captured), (
        "send failure must be logged, not swallowed (CLAUDE.md lesson #3)"
    )


@pytest.mark.asyncio
async def test_reply_instruction_warns_against_emoji():
    msg = ParsedMessage(
        message_id="ctx1",
        chat_id="u@im.wechat",
        sender_id="u@im.wechat",
        sender_name="u",
        content="hi",
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.PRIVATE,
        raw={"context_token": "ctx1"},
    )
    cred = WeChatCredential(
        agent_id="agent_x", bot_token="t", base_url="", enabled=True
    )
    info = await WeChatContextBuilder(
        message=msg, credential=cred, agent_id="agent_x"
    ).get_message_info()
    assert "emoji" in info["reply_instruction"].lower()
