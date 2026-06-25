"""Unit tests for the WeChat iLink SDK client — the gateway quirks must hold."""
import base64

import httpx
import pytest

from xyz_agent_context.module.wechat_module.wechat_sdk_client import (
    WeChatSDKClient,
    extract_text,
    ilink_headers,
)


def test_ilink_headers_shape():
    h = ilink_headers("tok123")
    assert h["AuthorizationType"] == "ilink_bot_token"
    assert h["Authorization"] == "Bearer tok123"
    # X-WECHAT-UIN is base64 of a decimal string (a random uint32).
    decoded = base64.b64decode(h["X-WECHAT-UIN"]).decode()
    assert decoded.isdigit()
    # QR-fetch is unauthenticated — no Bearer when token is empty.
    assert "Authorization" not in ilink_headers("")


def test_extract_text_concatenates_item_list():
    msg = {"item_list": [
        {"text_item": {"text": "hello "}},
        {"text_item": {"text": "world"}},
        {"image_item": {}},  # non-text item ignored
    ]}
    assert extract_text(msg) == "hello world"
    assert extract_text({}) == ""


def _client_with(handler) -> WeChatSDKClient:
    c = WeChatSDKClient("tok", "https://gw.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_get_updates_returns_payload_and_advances():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/ilink/bot/getupdates"
        # octet-stream content-type but JSON body is the documented quirk.
        return httpx.Response(200, json={"ret": 0, "get_updates_buf": "c2", "msgs": []})

    c = _client_with(handler)
    data = await c.get_updates("c1")
    assert data["get_updates_buf"] == "c2"
    await c.aclose()


@pytest.mark.asyncio
async def test_get_updates_raises_on_app_level_ret():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": 1001, "msgs": []})  # session expired

    c = _client_with(handler)
    with pytest.raises(RuntimeError, match="ret=1001"):
        await c.get_updates("c1")
    await c.aclose()


@pytest.mark.asyncio
async def test_send_message_chunks_long_text():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(req.content)
        seen.append(body["msg"]["item_list"][0]["text_item"]["text"])
        assert body["msg"]["to_user_id"] == "wxA"
        assert body["msg"]["context_token"] == "ctx"
        return httpx.Response(200, json={"ret": 0})

    c = _client_with(handler)
    ok = await c.send_message("wxA", "ctx", "x" * 4500)  # 2000 + 2000 + 500
    assert ok is True
    assert [len(s) for s in seen] == [2000, 2000, 500]
    await c.aclose()
