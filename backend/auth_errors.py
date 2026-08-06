"""
@file_name: auth_errors.py
@author: Bin Liang
@date: 2026-08-06
@description: One vocabulary for every 401 the backend can emit, plus the
logging that makes those rejections diagnosable.

Why this module exists
----------------------
A 401 used to be a single undifferentiated signal. The frontend saw only
the status code, so it could not tell these apart:

- the session JWT is expired / forged / absent  → the session IS dead
- a *second* credential failed (NetMind login token, Manyfold gateway
  token, an OpenAI-compatible API key)          → the session is fine
- the handler could not resolve an identity that the middleware already
  verified                                      → an internal bug

It treated all three as "your session is dead" and destroyed the whole
SPA session. On 2026-08-02 that fired repeatedly during a live demo: a
stale NetMind token on `/api/providers` bounced users to /login mid-run.

Every 401 now carries a `code`, and only ``SESSION_DEAD_CODES`` means
"log the user out". Anything a future author forgets to classify falls
outside that set and is therefore *safe by default* — the failure mode
of an unknown code is a local error, not a global logout.

The second half of the module is observability. The middleware used to
return a bare ``JSONResponse`` with no logger call at all, which is why
the 8/2 incident could not be diagnosed from server logs: we had ten
401s with a path and nothing else. ``log_auth_rejection`` emits one
``[auth-reject]`` line per rejection carrying the code and — for token
failures — the token's own iat/exp, which is what distinguishes a
naturally-expired token from one signed with a different key.
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

# =============================================================================
# The vocabulary
# =============================================================================

# --- Session death: only a re-login fixes these. -----------------------------
TOKEN_EXPIRED = "token_expired"
TOKEN_INVALID = "token_invalid"
TOKEN_MISSING = "token_missing"
# Local mode with no X-User-Id header: the frontend lost configStore.userId,
# so it has no identity to send. Not a token problem, but equally only a
# re-login repopulates it.
IDENTITY_MISSING = "identity_missing"

# --- Everything else: the session is intact; handle it locally. --------------
# Auth passed but request.state.user_id was empty when a handler asked for
# it — a wiring bug on our side, never a reason to end the user's session.
IDENTITY_UNRESOLVED = "identity_unresolved"
# The NetMind loginToken (X-Netmind-Token) is missing/stale. Unrelated to
# the NarraNexus session JWT — billing and the NetMind provider endpoints
# both authenticate with it.
NETMIND_TOKEN_INVALID = "netmind_token_invalid"
# MANYFOLD_GATEWAY_TOKEN rejection on a platform-class endpoint.
GATEWAY_TOKEN_INVALID = "gateway_token_invalid"
# OpenAI-compatible surface: the caller's API key is bad. The caller is a
# third-party client, not our SPA.
API_KEY_INVALID = "api_key_invalid"

SESSION_DEAD_CODES = frozenset({
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    TOKEN_MISSING,
    IDENTITY_MISSING,
})


class AuthError(HTTPException):
    """A 401 that says *why*.

    Route handlers raise this instead of ``HTTPException(401, ...)``.
    ``install_auth_error_handler`` renders it as
    ``{"detail": <message>, "code": <code>}`` — `detail` keeps the exact
    shape FastAPI already produced, so existing callers and log readers
    are unaffected; `code` is purely additive.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(status_code=401, detail=detail)
        self.code = code


# =============================================================================
# Observability
# =============================================================================

def _token_lifetime(token: Optional[str]) -> str:
    """Render a rejected token's own iat/exp claims for the log line.

    Decoded WITHOUT signature verification — this is diagnostics only and
    must never inform an authorization decision. Reading the claims of a
    token we just refused is exactly how we tell "expired seven days after
    a legitimate login" apart from "signed by a key this process doesn't
    have" (a redeploy with a rotated JWT_SECRET looks identical to the
    user, and produced the unanswered question of the 8/2 incident).
    """
    if not token:
        return ""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return " iat=? exp=? (undecodable)"
    return f" iat={claims.get('iat')} exp={claims.get('exp')} sub={claims.get('user_id')}"


def log_auth_rejection(
    code: str,
    detail: str,
    *,
    path: str,
    method: str = "",
    token: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Emit the one line that makes a 401 diagnosable after the fact."""
    logger.warning(
        f"[auth-reject] code={code} {method} {path} "
        f"user={user_id or '-'} detail={detail!r}{_token_lifetime(token)}"
    )


def auth_error_response(
    code: str,
    detail: str,
    *,
    path: str,
    method: str = "",
    token: Optional[str] = None,
    user_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> JSONResponse:
    """Build (and log) a 401 from middleware, where raising is not an option.

    Middleware runs outside the exception-handler chain, so it returns a
    response directly instead of raising ``AuthError``. Same body shape.
    """
    log_auth_rejection(
        code, detail, path=path, method=method, token=token, user_id=user_id
    )
    body = {"detail": detail, "code": code}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=401, content=body)


def install_auth_error_handler(app: FastAPI) -> None:
    """Render ``AuthError`` as ``{detail, code}`` and log it.

    Registered in backend/main.py. Without this, FastAPI's default
    HTTPException handler drops the `code` field entirely.
    """

    @app.exception_handler(AuthError)
    async def _handle_auth_error(request: Request, exc: AuthError) -> JSONResponse:
        log_auth_rejection(
            exc.code,
            str(exc.detail),
            path=request.url.path,
            method=request.method,
            user_id=getattr(request.state, "user_id", None),
        )
        return JSONResponse(
            status_code=401, content={"detail": exc.detail, "code": exc.code}
        )
