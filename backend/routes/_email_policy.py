"""
@file_name: _email_policy.py
@author: Bin Liang
@date: 2026-10-28
@description: Which email addresses the self-serve signup routes refuse.

POST /api/auth/signup/send-code and POST /api/auth/signup are
unauthenticated by definition, and send-code spends our NetMind mail channel.
Throwaway-mailbox services are how one actor mints many accounts (the
2026-10-28 incident registered from such domains), so both routes refuse them
before any rate-limit bucket is spent or any upstream call is made.

The list is deliberately small and explicit — well-known disposable services
only, reviewable in a diff — rather than a vendored third-party list that
changes under us and false-positives real providers. Operators extend it
without a release via ``NARRANEXUS_BLOCKED_EMAIL_DOMAINS`` (comma-separated),
read per call (cheap: one split) so tests and operators see the current env.

Matching is on whole DNS labels: a listed domain blocks itself and every
subdomain of it (``eu.mailinator.com``), never a look-alike
(``notmailinator.com``) or a name that merely CONTAINS it as a left label
(``mailinator.com.evil.org``).
"""
from __future__ import annotations

import os

BLOCKED_EMAIL_DOMAINS_ENV = "NARRANEXUS_BLOCKED_EMAIL_DOMAINS"

DISPOSABLE_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "10minutemail.com",
    "1secmail.com",
    "33mail.com",
    "anonbox.net",
    "armyspy.com",
    "burnermail.io",
    "cc.cd",
    "crazymailing.com",
    "cuvox.de",
    "dayrep.com",
    "dispostable.com",
    "dropmail.me",
    "einrot.com",
    "emailfake.com",
    "emailondeck.com",
    "fakeinbox.com",
    "fakemail.net",
    "fleckens.hu",
    "generator.email",
    "getnada.com",
    "guerrillamail.com",
    "gustr.com",
    "harakirimail.com",
    "inboxkitten.com",
    "jourrapide.com",
    "luxusmail.org",
    "mail-temp.com",
    "mail7.io",
    "mailcatch.com",
    "maildrop.cc",
    "mailinator.com",
    "mailnesia.com",
    "mailpoof.com",
    "mailsac.com",
    "mailtemp.net",
    "maxxspace.com",
    "mintemail.com",
    "moakt.com",
    "mohmal.com",
    "mytemp.email",
    "nimail.cn",
    "rhyta.com",
    "sharklasers.com",
    "spamgourmet.com",
    "superrito.com",
    "teleworm.us",
    "temp-mail.org",
    "tempail.com",
    "tempinbox.com",
    "tempmail.net",
    "tempmail.plus",
    "tempmailo.com",
    "tempr.email",
    "throwawaymail.com",
    "tmpmail.org",
    "trashmail.com",
    "yopmail.com",
})


def _normalise_domain(raw: str) -> str:
    return raw.strip().lower().lstrip("@").strip(".")


def _env_blocked_domains() -> frozenset[str]:
    raw = os.getenv(BLOCKED_EMAIL_DOMAINS_ENV) or ""
    return frozenset(d for d in (_normalise_domain(p) for p in raw.split(",")) if d)


def is_blocked_email(email: str) -> bool:
    """True when the address's domain, or any parent domain of it, is blocked.

    A string without ``@`` is never blocked here — address validity is the
    request model's job (``EmailStr``), not this policy's.
    """
    if "@" not in email:
        return False
    domain = _normalise_domain(email.rsplit("@", 1)[1])
    if not domain:
        return False
    blocked = DISPOSABLE_EMAIL_DOMAINS | _env_blocked_domains()
    labels = domain.split(".")
    return any(".".join(labels[i:]) in blocked for i in range(len(labels)))


__all__ = ["BLOCKED_EMAIL_DOMAINS_ENV", "DISPOSABLE_EMAIL_DOMAINS", "is_blocked_email"]
