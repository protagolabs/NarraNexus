"""
@file_name: test_netmind_billing_client.py
@author: NarraNexus
@date: 2026-07-02
@description: Unit tests for NetmindBillingClient (NetMind billing proxy).

Mocks the HTTP layer with httpx.MockTransport — no real network. Covers the
two-valued error contract (auth 401/403 -> BillingAuthError; 5xx/network ->
BillingUpstreamError) and the loginToken header contract.
"""
from __future__ import annotations

import httpx
import pytest

from xyz_agent_context.services.netmind_billing_client import (
    BillingAuthError,
    BillingBusinessError,
    BillingUpstreamError,
    NetmindBillingClient,
)

_ME_FREE = {
    "plan_id": "free",
    "name": "NetMind Free",
    "quota_limits": {"rpm": 60},
    "features": {"support": False, "member_price": False},
    "monthly_grant_usd": 0.0,
    "prices": [],
    "subscription": None,
}


def _client_with(handler) -> NetmindBillingClient:
    return NetmindBillingClient(
        base_url="https://billing.test.invalid",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_get_subscription_returns_flat_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ME_FREE)

    data = await _client_with(handler).get_subscription("jwt-abc")
    assert data["plan_id"] == "free"
    assert data["subscription"] is None


@pytest.mark.asyncio
async def test_get_subscription_sends_logintoken_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("loginToken")
        seen["path"] = request.url.path
        return httpx.Response(200, json=_ME_FREE)

    await _client_with(handler).get_subscription("jwt-abc")
    assert seen["auth"] == "Bearer jwt-abc"  # custom header, Bearer prefix
    assert seen["path"] == "/v1/power-subscription/me"


@pytest.mark.asyncio
async def test_get_plans_is_public_no_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("loginToken")
        return httpx.Response(200, json={"plans": []})

    data = await _client_with(handler).get_plans()
    assert data == {"plans": []}
    assert seen["auth"] is None  # no token forwarded for public catalog


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_auth_statuses_map_to_auth_error(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "Invalid API key"})

    with pytest.raises(BillingAuthError):
        await _client_with(handler).get_subscription("bad-jwt")


@pytest.mark.asyncio
async def test_5xx_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="oops")

    with pytest.raises(BillingUpstreamError):
        await _client_with(handler).get_subscription("jwt-abc")


@pytest.mark.asyncio
async def test_network_error_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(BillingUpstreamError):
        await _client_with(handler).get_plans()


@pytest.mark.asyncio
async def test_non_json_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(BillingUpstreamError):
        await _client_with(handler).get_plans()


# --- Phase 3: subscribe / cancel / reactivate ------------------------------

@pytest.mark.asyncio
async def test_subscribe_returns_checkout_and_posts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"session_id": "cs_1", "checkout_url": "https://x/y"})

    data = await _client_with(handler).subscribe("jwt")
    assert data["checkout_url"] == "https://x/y"
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/power-subscription/subscribe"


@pytest.mark.asyncio
async def test_cancel_returns_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "auto_renew_off"})

    data = await _client_with(handler).cancel("jwt")
    assert data["status"] == "auto_renew_off"


@pytest.mark.asyncio
async def test_reactivate_posts_to_reactivate_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "auto_renew_on"})

    await _client_with(handler).reactivate("jwt")
    assert seen["path"] == "/v1/power-subscription/reactivate"


@pytest.mark.asyncio
async def test_business_400_maps_to_business_error_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Already subscribed to Pro."})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).subscribe("jwt")
    assert ei.value.message == "Already subscribed to Pro."
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_business_400_extracts_detail_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "No active Pro subscription."})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).cancel("jwt")
    assert ei.value.message == "No active Pro subscription."


@pytest.mark.asyncio
async def test_business_message_scrubs_token_shaped_value():
    # If the upstream echoes a JWT-shaped value under an allowed key, it must
    # NOT be passed through (defense against token/PII leak into client + logs).
    jwt = "abcdefghij0123456789.klmnopqrstuvwx.yz0123456789ABCD"
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": jwt})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).subscribe("jwt")
    assert jwt not in ei.value.message
    assert ei.value.message  # falls back to a generic string
