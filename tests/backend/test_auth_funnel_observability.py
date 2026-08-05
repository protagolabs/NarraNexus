"""Auth-funnel observability: the 2026-08-01 post-mortem could not answer
"where did signups die" (signup 400x17 with no upstream detail recorded) or
"why couldn't fresh accounts log in" (browser->NetMind failures invisible to
the server). These tests pin the three closures:

  1. every upstream refusal is logged with the upstream's own msg —
     and never with the password or the verification code,
  2. a rejected NetMind login token carries upstream status/msg in the
     exception (never the token) so the route's 401 log can bucket them,
  3. the client-side blind spot has an ingest (/funnel-report): pre-auth by
     nature, log-only, rate-limited, log-forging-resistant.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

import backend.routes.auth as auth_mod
from backend.integrations.netmind.netmind_auth_client import (
    NetmindAuthClient,
    NetmindAuthError,
)
from backend.integrations.netmind.netmind_register_client import (
    NetmindRegisterClient,
    RegistrationError,
)

GOOD_PW = "Aa1!aaaa"


@pytest.fixture
def log_lines():
    """Capture loguru output for the duration of a test."""
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_limiter",
                        SlidingWindowRateLimiter(limit=10, window_sec=60.0))
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


# ── 1. upstream refusals are logged, secrets are not ───────────────────────

def _register_client_with(handler) -> NetmindRegisterClient:
    return NetmindRegisterClient(
        base_url="https://userauth.test.invalid",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_upstream_refusal_is_logged_with_upstream_msg(log_lines):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "msg": "verification code error"}
        )

    with pytest.raises(RegistrationError):
        await _register_client_with(handler).register(
            "a@b.com", GOOD_PW, "424242"
        )
    joined = "\n".join(log_lines)
    assert "[signup-funnel] upstream refusal" in joined
    assert "verification code error" in joined
    assert "a@b.com" in joined  # bucketing needs the email


@pytest.mark.asyncio
async def test_refusal_log_never_contains_password_or_code(log_lines):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "msg": "nope"})

    with pytest.raises(RegistrationError):
        await _register_client_with(handler).register(
            "a@b.com", GOOD_PW, "424242"
        )
    joined = "\n".join(log_lines)
    assert GOOD_PW not in joined
    assert "424242" not in joined


@pytest.mark.asyncio
async def test_upstream_5xx_logs_a_body_snippet(log_lines):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway from CDN</html>")

    from backend.integrations.netmind.netmind_register_client import (
        RegistrationUpstreamError,
    )
    with pytest.raises(RegistrationUpstreamError):
        await _register_client_with(handler).send_code("a@b.com")
    joined = "\n".join(log_lines)
    assert "[signup-funnel] upstream 5xx" in joined
    assert "bad gateway" in joined


# ── 2. rejected login tokens carry upstream detail, never the token ────────

def _auth_client_with(handler) -> NetmindAuthClient:
    return NetmindAuthClient(
        base_url="https://userauth.test.invalid",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_rejected_token_error_carries_status_and_msg_not_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "msg": "token expired"}
        )

    with pytest.raises(NetmindAuthError) as exc_info:
        await _auth_client_with(handler).verify_token("secret-jwt-value")
    message = str(exc_info.value)
    assert "token expired" in message
    assert "status=200" in message
    assert "secret-jwt-value" not in message


@pytest.mark.asyncio
async def test_rejected_token_via_4xx_carries_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(NetmindAuthError) as exc_info:
        await _auth_client_with(handler).verify_token("secret-jwt-value")
    assert "status=401" in str(exc_info.value)
    assert "secret-jwt-value" not in str(exc_info.value)


# ── 3. the client-side blind-spot ingest ───────────────────────────────────

def test_funnel_report_logs_the_stage_and_detail(client, log_lines):
    r = client.post("/api/auth/funnel-report", json={
        "stage": "netmind_email_login_failed",
        "email": "a@b.com",
        "detail": "password error",
    })
    assert r.status_code == 200
    joined = "\n".join(log_lines)
    assert "[login-funnel] client stage=netmind_email_login_failed" in joined
    assert "a@b.com" in joined
    assert "password error" in joined


def test_funnel_report_rejects_unknown_stages(client):
    r = client.post("/api/auth/funnel-report",
                    json={"stage": "made_up_stage", "detail": "x"})
    assert r.status_code == 400


def test_funnel_report_is_404_in_local_mode(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: False)
    r = client.post("/api/auth/funnel-report",
                    json={"stage": "netmind_email_login_failed"})
    assert r.status_code == 404


def test_funnel_report_rate_limit_accepts_silently(client, monkeypatch, log_lines):
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_limiter",
                        SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    body = {"stage": "netmind_email_login_failed", "email": "a@b.com", "detail": "x"}
    assert client.post("/api/auth/funnel-report", json=body).status_code == 200
    # Over the limit: still 200 (diagnostics must never add an error on top
    # of the failure the user is already looking at), but nothing logged.
    n_before = sum("[login-funnel] client" in ln for ln in log_lines)
    assert client.post("/api/auth/funnel-report", json=body).status_code == 200
    n_after = sum("[login-funnel] client" in ln for ln in log_lines)
    assert n_after == n_before


def test_funnel_report_strips_newlines_against_log_forging(client, log_lines):
    client.post("/api/auth/funnel-report", json={
        "stage": "signup_ui_error",
        "detail": "line1\nFAKE-LOG-LINE",
    })
    hit = next(ln for ln in log_lines if "[login-funnel] client" in ln)
    assert "line1 FAKE-LOG-LINE" in hit


def test_funnel_report_is_exempt_from_auth():
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/funnel-report" in AUTH_EXEMPT_PATHS
