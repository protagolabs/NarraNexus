"""
@file_name: verify.py
@author:
@date: 2026-08-10
@description: The ONE header-level identity verification, shared by every
consumer of the nx-agent bearer contract.

Both verifiers — the mcp middleware (identity/mcp_auth.py) and backend's
nx-agent service path (backend/auth.py) — run the identical algorithm: take
the explicit X-NarraNexus-Identity-Token header or bearer field #7, verify
the Ed25519 signature, and cross-check the self-declared bearer user_id
against the proven sub. Only their DEGRADATION differs (mcp fails open on a
missing key, backend fails closed), so that stays with the callers and the
algorithm lives here once — a third copy was already on the horizon
(broker/executor-side consumers) and near-identical copies drift.
"""
from __future__ import annotations

from typing import Optional, Union

from xyz_agent_context.module.identity.tokens import (
    IdentityTokenError,
    VerifiedIdentity,
    verify_identity_token,
)


def verify_caller_identity(
    headers, public_key: Union[str, bytes]
) -> tuple[Optional[VerifiedIdentity], str]:
    """Verify the identity token carried by ``headers`` against ``public_key``.

    ``headers`` is anything with a case-insensitive-enough ``.get`` (Starlette
    Headers, the middleware's scope wrapper, a plain dict from tests).

    Returns ``(identity, reason)``: identity None with a stable reason string
    ("no-token" / "invalid: …" / "user-id-mismatch: …") when no proof or bad
    proof. Reasons are for logs and each caller's own degradation policy —
    never flow control beyond that. Key-availability policy is deliberately
    NOT here: whether a missing public key fails open or closed is the one
    real difference between the verifiers.
    """
    from xyz_agent_context.module._mcp_identity import (
        IDENTITY_TOKEN_HEADER,
        parse_bearer_identity,
    )

    explicit = (
        headers.get(IDENTITY_TOKEN_HEADER.lower())
        or headers.get(IDENTITY_TOKEN_HEADER)
        or ""
    ).strip()
    bearer = parse_bearer_identity(
        headers.get("authorization") or headers.get("Authorization") or ""
    )
    token = explicit or bearer.identity_token
    if not token:
        return None, "no-token"
    try:
        identity = verify_identity_token(token, public_key)
    except IdentityTokenError as e:
        return None, f"invalid: {e}"
    if bearer.user_id and bearer.user_id != identity.user_id:
        # The self-declared bearer user_id disagreeing with the proven sub is
        # a forged field, not an unknown — the whole record is untrusted.
        return None, (
            f"user-id-mismatch: bearer says {bearer.user_id!r}, "
            f"token proves {identity.user_id!r}"
        )
    return identity, "ok"


__all__ = ["verify_caller_identity"]
