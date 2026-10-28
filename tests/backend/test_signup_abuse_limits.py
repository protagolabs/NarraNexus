"""
@file_name: test_signup_abuse_limits.py
@author: Bin Liang
@date: 2026-10-28
@description: Abuse limits on the two unauthenticated signup writes.

2026-10-28 incident: one IP self-registered accounts and could have used
POST /api/auth/signup/send-code as a mail cannon — the only limit was one
code per EMAIL per minute, so a fresh address per request was unlimited.
These tests pin the closures:

  1. per-client-IP sliding-window buckets on send-code (minute + hour) and
     on signup (hour), keyed by the shared ``client_ip`` helper — the N+1th
     request from one IP is 429 with Retry-After while another IP passes,
  2. the per-email limits still apply on top,
  3. throwaway-mailbox domains (and their subdomains) are refused with a
     neutral 400 BEFORE any bucket is spent or any upstream call is made,
  4. the per-IP numbers are env-configurable with forgiving parsing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.auth as auth_mod
from backend.routes._rate_limiter import SlidingWindowRateLimiter, env_limit

GOOD_PW = "Aa1!aaaa"
PROXY_HOP = "172.18.0.5"  # the caddy container nginx appends


def _xff(ip: str) -> dict:
    # client -> caddy -> nginx -> backend: the edge-appended entry is 2nd
    # from the right (TRUSTED_PROXY_HOPS default).
    return {"X-Forwarded-For": f"{ip}, {PROXY_HOP}"}


def _fresh(limit: int, window: float) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(limit=limit, window_sec=window)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    monkeypatch.delenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", raising=False)
    # Fresh buckets at the SHIPPED limits — module state must not leak.
    monkeypatch.setattr(auth_mod, "_signup_code_limiter", _fresh(1, 60.0))
    monkeypatch.setattr(auth_mod, "_signup_attempt_limiter", _fresh(10, 600.0))
    monkeypatch.setattr(
        auth_mod, "_signup_code_ip_minute_limiter",
        _fresh(auth_mod.SIGNUP_CODE_PER_IP_PER_MINUTE, 60.0),
    )
    monkeypatch.setattr(
        auth_mod, "_signup_code_ip_hour_limiter",
        _fresh(auth_mod.SIGNUP_CODE_PER_IP_PER_HOUR, 3600.0),
    )
    monkeypatch.setattr(
        auth_mod, "_signup_ip_limiter",
        _fresh(auth_mod.SIGNUP_PER_IP_PER_HOUR, 3600.0),
    )
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


@pytest.fixture
def upstream(monkeypatch):
    stub = AsyncMock()
    import backend.integrations.netmind.netmind_register_client as mod
    monkeypatch.setattr(mod.NetmindRegisterClient, "send_code", stub, raising=False)
    monkeypatch.setattr(mod.NetmindRegisterClient, "register", stub, raising=False)
    return stub


def _send_code(client, email, ip):
    return client.post(
        "/api/auth/signup/send-code", json={"email": email}, headers=_xff(ip)
    )


def _signup(client, email, ip):
    return client.post(
        "/api/auth/signup",
        json={"email": email, "password": GOOD_PW, "verify_code": "123456"},
        headers=_xff(ip),
    )


# ── shipped defaults ───────────────────────────────────────────────────────

def test_shipped_defaults():
    assert auth_mod.SIGNUP_CODE_PER_IP_PER_MINUTE == 5
    assert auth_mod.SIGNUP_CODE_PER_IP_PER_HOUR == 30
    assert auth_mod.SIGNUP_PER_IP_PER_HOUR == 5


# ── 1. per-IP buckets ──────────────────────────────────────────────────────

def test_send_code_per_ip_minute_bucket_rotating_emails(client, upstream):
    """A fresh email per request used to be unlimited: one IP could aim our
    mail channel at any number of mailboxes."""
    limit = auth_mod.SIGNUP_CODE_PER_IP_PER_MINUTE
    for i in range(limit):
        assert _send_code(client, f"victim{i}@example.com", "51.0.0.7").status_code == 200
    r = _send_code(client, "victim-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    retry_after = int(r.headers["Retry-After"])
    assert 1 <= retry_after <= 60
    # A different client IP has its own bucket.
    assert _send_code(client, "someone@example.com", "51.0.0.8").status_code == 200
    assert upstream.await_count == limit + 1


def test_send_code_per_ip_hour_bucket(client, upstream, monkeypatch):
    # Neutralise the minute bucket so the hour bucket is what is under test.
    monkeypatch.setattr(auth_mod, "_signup_code_ip_minute_limiter", _fresh(10_000, 60.0))
    limit = auth_mod.SIGNUP_CODE_PER_IP_PER_HOUR
    for i in range(limit):
        assert _send_code(client, f"v{i}@example.com", "51.0.0.7").status_code == 200
    r = _send_code(client, "v-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert 60 < int(r.headers["Retry-After"]) <= 3600
    assert _send_code(client, "other@example.com", "51.0.0.9").status_code == 200


def test_send_code_ip_is_the_edge_entry_not_the_forged_first_hop(client, upstream):
    """Rotating a forged leading XFF entry must not mint a new bucket."""
    limit = auth_mod.SIGNUP_CODE_PER_IP_PER_MINUTE
    for i in range(limit):
        r = client.post(
            "/api/auth/signup/send-code",
            json={"email": f"f{i}@example.com"},
            headers={"X-Forwarded-For": f"6.6.6.{i}, 51.0.0.7, {PROXY_HOP}"},
        )
        assert r.status_code == 200
    r = client.post(
        "/api/auth/signup/send-code",
        json={"email": "f-next@example.com"},
        headers={"X-Forwarded-For": f"6.6.6.99, 51.0.0.7, {PROXY_HOP}"},
    )
    assert r.status_code == 429


def test_send_code_per_email_limit_still_applies(client, upstream):
    assert _send_code(client, "a@example.com", "51.0.0.7").status_code == 200
    r = _send_code(client, "a@example.com", "51.0.0.8")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_send_code_per_ip_rejection_allocates_no_email_keys(client, upstream):
    """Per-IP runs before the caller-keyed per-email bucket: a flood rejected
    by per-IP must not grow the per-email key space."""
    limit = auth_mod.SIGNUP_CODE_PER_IP_PER_MINUTE
    for i in range(limit):
        _send_code(client, f"warm{i}@example.com", "51.0.0.7")
    keys_before = len(auth_mod._signup_code_limiter._deques)
    for i in range(5):
        assert _send_code(client, f"flood{i}@example.com", "51.0.0.7").status_code == 429
    assert len(auth_mod._signup_code_limiter._deques) == keys_before


def test_signup_per_ip_hour_bucket(client, upstream):
    limit = auth_mod.SIGNUP_PER_IP_PER_HOUR
    for i in range(limit):
        assert _signup(client, f"new{i}@example.com", "51.0.0.7").status_code == 200
    r = _signup(client, "new-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert 1 <= int(r.headers["Retry-After"]) <= 3600
    assert _signup(client, "new-other@example.com", "51.0.0.8").status_code == 200
    assert upstream.await_count == limit + 1


def test_signup_per_email_limit_still_applies(client, upstream, monkeypatch):
    monkeypatch.setattr(auth_mod, "_signup_ip_limiter", _fresh(10_000, 3600.0))
    for _ in range(10):
        assert _signup(client, "same@example.com", "51.0.0.7").status_code == 200
    r = _signup(client, "same@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_weak_password_does_not_spend_the_ip_bucket(client, upstream):
    """Policy rejects spend nothing upstream, so they must not lock a real
    user's network out of signup for an hour."""
    for _ in range(auth_mod.SIGNUP_PER_IP_PER_HOUR + 2):
        r = client.post(
            "/api/auth/signup",
            json={"email": "a@example.com", "password": "weak", "verify_code": "1"},
            headers=_xff("51.0.0.7"),
        )
        assert r.status_code == 400
    assert _signup(client, "a@example.com", "51.0.0.7").status_code == 200


# ── 3. disposable-email rejection ──────────────────────────────────────────

@pytest.mark.parametrize("email", [
    "x@mailinator.com",
    "x@MAILINATOR.com",
    "x@eu.mailinator.com",      # subdomain of a listed domain
    "x@yopmail.com",
    "x@foo.cc.cd",
])
def test_send_code_refuses_disposable_domains(client, upstream, email):
    r = _send_code(client, email, "51.0.0.7")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "disposable" not in detail.lower()  # neutral wording
    upstream.assert_not_awaited()


def test_signup_refuses_disposable_domains(client, upstream):
    r = _signup(client, "x@guerrillamail.com", "51.0.0.7")
    assert r.status_code == 400
    upstream.assert_not_awaited()


def test_blocked_domain_spends_no_bucket(client, upstream):
    for _ in range(auth_mod.SIGNUP_CODE_PER_IP_PER_MINUTE + 3):
        assert _send_code(client, "x@mailinator.com", "51.0.0.7").status_code == 400
    assert _send_code(client, "real@example.com", "51.0.0.7").status_code == 200


def test_normal_domain_and_lookalike_pass(client, upstream):
    assert _send_code(client, "a@gmail.com", "51.0.0.7").status_code == 200
    # Suffix match is on label boundaries: notmailinator.com is not listed.
    assert _send_code(client, "a@notmailinator.com", "51.0.0.8").status_code == 200


def test_env_extends_the_blocklist(client, upstream, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", " Burner-Mail.io , @other-burner.org ")
    assert _send_code(client, "a@burner-mail.io", "51.0.0.7").status_code == 400
    assert _send_code(client, "a@sub.other-burner.org", "51.0.0.7").status_code == 400
    assert _send_code(client, "a@example.com", "51.0.0.7").status_code == 200


# ── 4. env parsing ─────────────────────────────────────────────────────────

def test_env_limit_parsing(monkeypatch):
    name = "NARRANEXUS_TEST_LIMIT"
    monkeypatch.delenv(name, raising=False)
    assert env_limit(name, 5) == 5
    for raw, want in (("", 5), ("abc", 5), ("0", 5), ("-2", 5), ("12", 12), (" 7 ", 7)):
        monkeypatch.setenv(name, raw)
        assert env_limit(name, 5) == want, raw


def test_retry_after_counts_down_from_the_oldest_hit(monkeypatch):
    import backend.routes._rate_limiter as rl

    now = [1000.0]
    monkeypatch.setattr(rl, "monotonic", lambda: now[0])
    lim = SlidingWindowRateLimiter(limit=2, window_sec=60.0)
    assert lim.allow("k") and lim.allow("k")
    now[0] += 20.5
    assert lim.allow("k") is False
    assert lim.retry_after("k") == 40  # ceil(60 - 20.5)
    assert SlidingWindowRateLimiter(limit=0, window_sec=60.0).retry_after("x") == 60
