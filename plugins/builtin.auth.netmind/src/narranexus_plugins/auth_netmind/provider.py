"""
@file_name: provider.py
@author: Bin Liang
@date: 2026-09-04
@description: `kernel.auth` provider of the cloud distribution: identity = the NetMind-login JWT in `Authorization: Bearer`. No bearer = no identity (the middleware answers per path); an expired or invalid token raises `AuthError` with the same codes the middleware used to emit, so the SPA keeps its session semantics.
"""
from __future__ import annotations

from typing import Any, Mapping

import jwt

from backend.auth_errors import TOKEN_EXPIRED, TOKEN_INVALID, AuthError
from narranexus.kernel.plugins.registry import Contribution


class NetMindAuthProvider:
    """``AuthProvider`` for the cloud build: identity = the decoded JWT bearer."""

    id = "builtin.auth.netmind"
    scheme = "bearer"

    async def authenticate(self, request: Any) -> Mapping[str, Any] | None:
        header = request.headers.get("Authorization", "") or ""
        if not header.startswith("Bearer "):
            return None
        from backend.auth import decode_token

        token = header[7:]
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            raise AuthError(TOKEN_EXPIRED, "Token expired") from None
        except jwt.InvalidTokenError:
            raise AuthError(TOKEN_INVALID, "Invalid token") from None
        return {"user_id": payload["user_id"], "role": payload.get("role", "user"), "provider": self.id, "token": token}


CONTRIBUTION = Contribution("netmind", lambda: NetMindAuthProvider())

__all__ = ["CONTRIBUTION", "NetMindAuthProvider"]
