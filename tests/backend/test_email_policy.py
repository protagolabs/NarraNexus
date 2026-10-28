"""
@file_name: test_email_policy.py
@author: Bin Liang
@date: 2026-10-28
@description: The disposable-mailbox blocklist used by the signup routes.
"""
from __future__ import annotations

import pytest

from backend.routes._email_policy import DISPOSABLE_EMAIL_DOMAINS, is_blocked_email


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", raising=False)


def test_list_is_explicit_and_normalised():
    assert len(DISPOSABLE_EMAIL_DOMAINS) >= 50
    for d in DISPOSABLE_EMAIL_DOMAINS:
        assert d == d.strip().lower() and not d.startswith(".") and "@" not in d


@pytest.mark.parametrize("email,blocked", [
    ("a@mailinator.com", True),
    ("a@Mailinator.COM", True),
    ("a@x.y.mailinator.com", True),
    ("a@mailinator.com.", True),          # trailing root dot
    ("a@10minutemail.com", True),
    ("a@maxxspace.com", True),
    ("a@nimail.cn", True),
    ("a@cc.cd", True),
    ("a@gmail.com", False),
    ("a@mailinator.com.evil.org", False),  # listed name as a LEFT label
    ("a@notyopmail.com", False),
    ("not-an-email", False),
])
def test_matching(email, blocked):
    assert is_blocked_email(email) is blocked


def test_env_extension(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", "corp-burner.io,, .Dots.Example.")
    assert is_blocked_email("a@corp-burner.io")
    assert is_blocked_email("a@mx.dots.example")
    assert not is_blocked_email("a@gmail.com")
