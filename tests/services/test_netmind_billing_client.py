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
