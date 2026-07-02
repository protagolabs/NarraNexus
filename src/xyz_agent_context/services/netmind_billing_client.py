"""
@file_name: netmind_billing_client.py
@author: NetMind.AI
@date: 2026-07-02
@description: NetMind billing/subscription API proxy client.

Thin async client the NarraNexus backend uses to call NetMind's
billing+subscription API on behalf of a logged-in user. The user's NetMind
`loginToken` (JWT) is held by the frontend and forwarded per-request — we
NEVER store it. This client only wraps the HTTP call + error mapping so the
routes stay thin.

Mirrors ``netmind_auth_client.NetmindAuthClient`` deliberately:
- injectable ``transport`` so unit tests use ``httpx.MockTransport`` (no net).
- two-valued errors: BillingAuthError (bad/expired token -> caller 401) vs
  BillingUpstreamError (NetMind unreachable / broke contract -> caller 502).

Auth header quirks (verified against dev 2026-07-02 live probe):
- Subscription endpoints (``/v1/power-subscription/*``) authenticate with the
  custom header ``loginToken: Bearer <jwt>`` and return their JSON flat at the
  top level (NOT wrapped in {success, data}).
- Missing/invalid credentials return 401 on power-subscription; the sibling
  finance service returns 403 instead — both mean "auth failed", so BOTH are
  mapped to BillingAuthError.
- ``/v1/power-subscription/plan`` is public (no token).

Scope note (Phase 1): only ``get_plans`` + ``get_subscription`` are needed for
the account/status panel. Balance (user-fee-info), subscribe/cancel, and
recharge land in later phases on the same client.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from loguru import logger

DEFAULT_BASE_URL_ENV = "BILLING_API_BASE"
DEFAULT_TIMEOUT_ENV = "BILLING_API_TIMEOUT_SECONDS"
_FALLBACK_TIMEOUT_SECONDS = 10.0

# HTTP statuses that mean "the loginToken was rejected" (user's problem -> 401).
# power-subscription uses 401; the finance service uses 403 for the same thing.
_AUTH_FAIL_STATUSES = (401, 403)


class BillingAuthError(Exception):
    """The NetMind loginToken is invalid / expired / rejected (caller -> 401)."""


class BillingUpstreamError(Exception):
    """NetMind billing API unreachable or returned an unusable response (caller -> 502)."""


class NetmindBillingClient:
    """Thin async client around NetMind's billing+subscription API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get(DEFAULT_BASE_URL_ENV, "")).rstrip("/")
        if timeout_seconds is None:
            timeout_seconds = float(
                os.environ.get(DEFAULT_TIMEOUT_ENV, _FALLBACK_TIMEOUT_SECONDS)
            )
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def get_plans(self) -> Any:
        """Public plan catalog (Free / Pro). No token required.

        Returns the parsed JSON body (``{"plans": [...]}``).
        """
        return await self._request("GET", "/v1/power-subscription/plan")

    async def get_subscription(self, login_token: str) -> Any:
        """Current plan + subscription status for the token's user.

        Returns the flat JSON body: ``{plan_id, name, quota_limits, features,
        monthly_grant_usd, prices, subscription: null | {...}}``.
        """
        return await self._request(
            "GET",
            "/v1/power-subscription/me",
            login_token=login_token,
        )

    async def _request(
        self,
        method: str,
        path: str,
        login_token: Optional[str] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        """Issue one billing call, mapping transport/auth failures to the
        two-valued error contract. Never logs the loginToken."""
        if not self.base_url and self._transport is None:
            raise BillingUpstreamError(f"{DEFAULT_BASE_URL_ENV} is not configured")

        headers: dict[str, str] = {}
        if login_token:
            # NetMind convention: custom header named `loginToken`, Bearer prefix.
            headers["loginToken"] = f"Bearer {login_token}"

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout_seconds
            ) as http:
                response = await http.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise BillingUpstreamError(
                f"NetMind billing API unreachable: {exc}"
            ) from exc

        if response.status_code in _AUTH_FAIL_STATUSES:
            raise BillingAuthError("NetMind rejected the loginToken")
        if response.status_code >= 500:
            raise BillingUpstreamError(
                f"NetMind billing API returned {response.status_code}"
            )
        if response.status_code >= 400:
            # 4xx that isn't an auth failure (e.g. 400 business error). Surface
            # ONLY a short "message" field (if present) so a future route can
            # translate it (e.g. "Already subscribed") — never dump the whole
            # upstream body: it may echo the token or, in later phases,
            # payment/PII fields, and this string flows into server logs.
            msg = ""
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("message"), str):
                    msg = body["message"][:200]
            except ValueError:
                pass
            raise BillingUpstreamError(
                f"NetMind billing API returned {response.status_code}"
                + (f": {msg}" if msg else "")
            )

        try:
            return response.json()
        except ValueError as exc:
            raise BillingUpstreamError(
                "NetMind billing API returned non-JSON"
            ) from exc
