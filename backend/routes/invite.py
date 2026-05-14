"""
@file_name: invite.py
@author: NarraNexus
@date: 2026-05-14
@description: Public invite-code request endpoint.

POST /api/invite/request — a visitor submits their email; we generate a
unique invite code, email it to them, and (in Mode B) either issue it
immediately or waitlist it once the auto-issue cap is hit.

This route is JWT-exempt (it's the pre-registration funnel — the caller
has no account yet). It is the ONLY public surface; admin operations live
under /api/admin/invite. The generated code is delivered ONLY by email —
it is never returned in the HTTP response, so the rate limiter can't be
bypassed by reading the response body.

See drafts/logs/invite_code_2026_05_14.md.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from loguru import logger

from backend.auth import _is_cloud_mode
from backend.config import settings
from backend.routes._rate_limiter import SlidingWindowRateLimiter
from xyz_agent_context.repository import InviteCodeRepository
from xyz_agent_context.schema import InviteRequestRequest, InviteRequestResponse
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.mailer import send_email

router = APIRouter()

# Deliberately permissive — a full RFC 5322 validator is overkill; we just
# want to reject obvious garbage before spending a DB round-trip / email.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Process-local rate limiters (single FastAPI process today; abuse beyond
# this is caught by the Cloudflare Turnstile layer the website proxy adds).
#  - per IP:    5 requests / 10 min — blunt anti-spam
#  - per email: 3 requests / hour   — one inbox can't farm codes
_ip_limiter = SlidingWindowRateLimiter(limit=5, window_sec=600)
_email_limiter = SlidingWindowRateLimiter(limit=3, window_sec=3600)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _invite_email_body(code: str) -> tuple[str, str]:
    """Return (plain_text, html) bodies for the invite-code email."""
    text = (
        "Welcome to NarraNexus!\n\n"
        f"Your invite code is: {code}\n\n"
        "Create your account at https://agent.narra.nexus and enter this "
        "code when prompted.\n\n"
        "This code can be used once. If you didn't request it, you can "
        "safely ignore this email.\n"
    )
    html = (
        "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,"
        "sans-serif;font-size:15px;line-height:1.6;color:#1a1a1a\">"
        "<p>Welcome to <strong>NarraNexus</strong>!</p>"
        "<p>Your invite code is:</p>"
        f"<p style=\"font-size:22px;font-weight:700;letter-spacing:2px;"
        f"font-family:ui-monospace,SFMono-Regular,Menlo,monospace\">{code}</p>"
        "<p>Create your account at "
        "<a href=\"https://agent.narra.nexus\">agent.narra.nexus</a> and "
        "enter this code when prompted.</p>"
        "<p style=\"color:#666;font-size:13px\">This code can be used once. "
        "If you didn't request it, you can safely ignore this email.</p>"
        "</div>"
    )
    return text, html


async def _send_code_email(code: str, email: str) -> bool:
    text, html = _invite_email_body(code)
    return await send_email(
        to=email,
        subject="Your NarraNexus invite code",
        body_text=text,
        body_html=html,
    )


@router.post("/request", response_model=InviteRequestResponse)
async def request_invite(
    payload: InviteRequestRequest, request: Request
) -> InviteRequestResponse:
    if not _is_cloud_mode():
        return InviteRequestResponse(
            success=False,
            error="Invite codes are only used by the cloud version.",
        )

    email = (payload.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return InviteRequestResponse(
            success=False, error="Please enter a valid email address."
        )

    if not _ip_limiter.allow(_client_ip(request)):
        return InviteRequestResponse(
            success=False,
            error="Too many requests. Please try again later.",
        )
    if not _email_limiter.allow(email):
        return InviteRequestResponse(
            success=False,
            error="Too many requests for this email. Please try again later.",
        )

    try:
        db = await get_db_client()
        repo = InviteCodeRepository(db)
        existing = await repo.list_for_email(email)

        # ── Idempotency: one email never farms more than one live code ──
        if any(c.status == "used" for c in existing):
            return InviteRequestResponse(
                success=True,
                status="already_registered",
                message=(
                    "This email has already been used to register. "
                    "Please sign in instead."
                ),
            )

        issued = next((c for c in existing if c.status == "issued"), None)
        if issued:
            # Re-send the SAME code — never mint a second one for one email.
            sent = await _send_code_email(issued.code, email)
            await repo.mark_email_sent(issued.code, sent)
            return InviteRequestResponse(
                success=True,
                status="issued",
                message=(
                    "An invite code was already issued to this email — "
                    "we've re-sent it. Check your inbox (and spam)."
                ),
            )

        if any(c.status == "waitlisted" for c in existing):
            return InviteRequestResponse(
                success=True,
                status="waitlisted",
                message=(
                    "You're already on the waitlist. We'll email your "
                    "invite code when a spot opens up."
                ),
            )

        # ── New request — Mode B auto-issue cap ──
        active = await repo.count_active()
        if active >= settings.invite_auto_issue_cap:
            await repo.create(email, status="waitlisted", source="website")
            logger.info(
                "invite: cap {} reached, waitlisted {}",
                settings.invite_auto_issue_cap,
                email,
            )
            return InviteRequestResponse(
                success=True,
                status="waitlisted",
                message=(
                    "We've reached capacity for now — you're on the "
                    "waitlist and we'll email you when a spot opens up."
                ),
            )

        code_row = await repo.create(email, status="issued", source="website")
        sent = await _send_code_email(code_row.code, email)
        await repo.mark_email_sent(code_row.code, sent)
        logger.info(
            "invite: issued code to {} (email_sent={})", email, sent
        )
        return InviteRequestResponse(
            success=True,
            status="issued",
            message=(
                "Invite code sent — check your email, including the spam "
                "folder."
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("invite request failed for {}: {}", email, e)
        return InviteRequestResponse(
            success=False,
            error="Something went wrong. Please try again later.",
        )
