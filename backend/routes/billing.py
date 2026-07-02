"""
@file_name: billing.py
@author: NetMind.AI
@date: 2026-07-02
@description: Backend proxy for NetMind billing/subscription API.

The user's NetMind loginToken lives in the frontend (configStore.netmindToken);
it is forwarded per-request via the ``X-Netmind-Token`` header and proxied to
NetMind's billing API. We never store or log the token — this layer only adds
the HTTP envelope, cloud gating, and error mapping (D-1: backend proxy).

Cloud-only: the panel is a cloud-web feature. Gate uses the CANONICAL
``utils.deployment_mode.is_cloud_mode()`` (respects NARRANEXUS_DEPLOYMENT_MODE),
NOT providers.py::_is_cloud() which only checks DATABASE_URL — the latter can't
be flipped for a local cloud-smoke run and is not the project's canonical
resolver.

Phase 1 scope: GET /plans (public), GET /subscription (loginToken). Balance,
subscribe/cancel, and recharge land in later phases on the same proxy.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.deployment_mode import is_cloud_mode
from xyz_agent_context.services.netmind_billing_client import (
    BillingAuthError,
    BillingUpstreamError,
    NetmindBillingClient,
)

router = APIRouter()

_NETMIND_TOKEN_HEADER = "X-Netmind-Token"


def _client() -> NetmindBillingClient:
    """Build a billing client from settings. Cheap; no shared mutable state."""
    return NetmindBillingClient(
        base_url=settings.billing_api_base,
        timeout_seconds=settings.billing_api_timeout_seconds,
    )


def _require_cloud() -> None:
    """404 outside cloud mode — the billing panel is a cloud-web feature."""
    if not is_cloud_mode():
        raise HTTPException(status_code=404, detail="Not available in local mode")


def _require_netmind_token(request: Request) -> str:
    """Extract the user's NetMind loginToken from the request header.

    The frontend holds it in configStore.netmindToken and sends it on every
    billing call. Missing -> 401 (the user must (re-)authenticate with NetMind).
    """
    token = request.headers.get(_NETMIND_TOKEN_HEADER, "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail=f"Missing NetMind token ({_NETMIND_TOKEN_HEADER} header)",
        )
    return token


@router.get("/plans")
async def get_plans(request: Request):
    """Public plan catalog (Free / Pro). Cloud-only; no NetMind token needed."""
    _require_cloud()
    try:
        data = await _client().get_plans()
    except BillingUpstreamError as exc:
        logger.error(f"[billing] get_plans upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    return {"success": True, "data": data}


@router.get("/subscription")
async def get_subscription(request: Request):
    """Current plan + subscription status for the logged-in user.

    Identity is established locally (auth_middleware -> resolve_current_user_id);
    the NetMind loginToken is forwarded to identify the user on NetMind's side.
    """
    _require_cloud()
    # Establish local identity first (rejects unauthenticated callers).
    await resolve_current_user_id(request)
    token = _require_netmind_token(request)
    try:
        data = await _client().get_subscription(token)
    except BillingAuthError:
        # Bad / expired loginToken -> 401 so the frontend re-auths with NetMind.
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
    except BillingUpstreamError as exc:
        logger.error(f"[billing] get_subscription upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    return {"success": True, "data": data}
