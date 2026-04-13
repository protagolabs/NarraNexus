"""
@file_name: test_rate_limiter.py
@description: T14 — sliding-window rate limiter.
"""
import pytest

from backend.routes._rate_limiter import SlidingWindowRateLimiter


def test_allows_up_to_limit_per_window(monkeypatch):
    rl = SlidingWindowRateLimiter(limit=2, window_sec=1.0)
    t = [100.0]
    monkeypatch.setattr("backend.routes._rate_limiter.monotonic", lambda: t[0])
    assert rl.allow("u1") is True
    assert rl.allow("u1") is True
    assert rl.allow("u1") is False


def test_recovers_after_window(monkeypatch):
    rl = SlidingWindowRateLimiter(limit=2, window_sec=1.0)
    t = [100.0]
    monkeypatch.setattr("backend.routes._rate_limiter.monotonic", lambda: t[0])
    assert rl.allow("u1")
    assert rl.allow("u1")
    assert not rl.allow("u1")
    t[0] = 101.5
    assert rl.allow("u1") is True


def test_different_keys_are_independent(monkeypatch):
    rl = SlidingWindowRateLimiter(limit=2, window_sec=1.0)
    t = [100.0]
    monkeypatch.setattr("backend.routes._rate_limiter.monotonic", lambda: t[0])
    assert rl.allow("u1") and rl.allow("u1")
    assert rl.allow("u2")  # u1 limit reached but u2 fresh


def test_idle_cleanup_removes_empty_deques(monkeypatch):
    rl = SlidingWindowRateLimiter(limit=2, window_sec=1.0, cleanup_interval=3)
    t = [100.0]
    monkeypatch.setattr("backend.routes._rate_limiter.monotonic", lambda: t[0])
    rl.allow("u1")
    rl._deques["u1"].clear()  # simulate aged-out
    t[0] = 200.0  # push far into future so cleanup treats everything idle
    rl.allow("u2")
    rl.allow("u3")  # triggers cleanup (count % interval == 0 at 3rd call)
    assert "u1" not in rl._deques
