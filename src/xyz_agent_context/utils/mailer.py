"""
@file_name: mailer.py
@author: NarraNexus
@date: 2026-05-14
@description: Generic transactional email sender (SMTP).

A thin, provider-agnostic mailer. The default (and only) transport is
stdlib ``smtplib`` driven over ``asyncio.to_thread`` so it works with any
SMTP server — a personal Gmail App Password for early testing, or AWS SES /
Resend SMTP for production — by changing environment variables only, no
code change.

This module is GENERIC on purpose: it knows how to send an email, not what
to say. Scenario-specific content (e.g. the invite-code email body) is
composed by the caller. See铁律 #4 — 通用逻辑与场景特定逻辑分离.

Configuration (environment variables):
  SMTP_HOST       enables the mailer; when unset, send_email() is a logged
                  no-op that returns False (callers must not crash on this)
  SMTP_PORT       default 587
  SMTP_USER       login user (optional — omitted for unauthenticated relays)
  SMTP_PASSWORD   login password / app password
  SMTP_FROM       From: header; defaults to SMTP_USER
  SMTP_USE_TLS    "true" (default) → STARTTLS; "false" → plaintext
"""

from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage

from loguru import logger


def _smtp_host() -> str | None:
    return os.environ.get("SMTP_HOST") or None


def is_configured() -> bool:
    """True when SMTP_HOST is set — i.e. the mailer can actually send."""
    return _smtp_host() is not None


def _send_sync(
    to: str, subject: str, body_text: str, body_html: str | None
) -> None:
    """Blocking SMTP send — run via asyncio.to_thread, never on the loop."""
    host = _smtp_host()
    assert host is not None  # guarded by is_configured() in send_email
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER") or None
    password = os.environ.get("SMTP_PASSWORD") or None
    sender = os.environ.get("SMTP_FROM") or user or "no-reply@narra.nexus"
    use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    with smtplib.SMTP(host, port, timeout=20) as server:
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)


async def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    """Send one email. Returns True on success, False on any failure.

    Never raises — a mail failure must not abort the calling request flow
    (e.g. an invite code is still generated and persisted even if delivery
    fails; the admin list surfaces `email_sent = 0` for a manual re-send).
    """
    if not is_configured():
        logger.warning(
            "mailer: SMTP_HOST not configured — skipping email to {}", to
        )
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, body_text, body_html)
        logger.info("mailer: sent email to {} (subject={!r})", to, subject)
        return True
    except Exception as e:  # noqa: BLE001 — deliberately swallow all errors
        logger.exception("mailer: failed to send email to {}: {}", to, e)
        return False
