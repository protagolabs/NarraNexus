"""
@file_name: agent_api_key_schema.py
@author: NarraNexus
@date: 2026-06-11
@description: Pydantic schemas for the agent_api_keys management API.

External API protocol (v0.3). These types back:
  - GET    /api/agents/{agent_id}/api-keys              (list)
  - POST   /api/agents/{agent_id}/api-keys              (create — one-time
                                                         plaintext reveal)
  - PATCH  /api/agents/{agent_id}/api-keys/{key_id}     (rename / scopes /
                                                         expiry)
  - DELETE /api/agents/{agent_id}/api-keys/{key_id}     (soft revoke)
  - POST   /api/agents/{agent_id}/api-keys/{key_id}/rotate

The row in DB (`agent_api_keys` in schema_registry.py) holds the SHA256 of
the plaintext token; the plaintext is shown to the owner exactly once at
creation time and never recoverable thereafter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from xyz_agent_context.utils.timezone import utc_now


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo if dt is naive. Storage convention is UTC, so
    naive rows from a DB driver (e.g. SQLite returning bare
    'YYYY-MM-DD HH:MM:SS' strings) are treated as UTC rather than
    raising on the next comparison.
    """
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        return dt.replace(tzinfo=_tz.utc)
    return dt


# =============================================================================
# Internal entity (DB row representation)
# =============================================================================


class AgentApiKey(BaseModel):
    """One row in the `agent_api_keys` table — internal representation."""

    id: Optional[int] = None
    key_id: str
    token_hash: str  # SHA256 hex digest — never leaves the server
    token_prefix: str
    agent_id: str
    owner_user_id: str
    name: str
    scopes: List[str] = Field(default_factory=lambda: [
        "chat", "session.delete", "session.list"
    ])
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def is_active(self) -> bool:
        """A key is active when not revoked and not expired.

        Both sides of the comparison MUST be timezone-aware UTC.
        `expires_at` is stored UTC and `_parse_dt` in the repository
        normalises naive DB rows to UTC, so we use `utc_now()` (NOT
        `datetime.now()`) to keep the comparison sane.
        """
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and _ensure_utc(self.expires_at) < utc_now():
            return False
        return True

    def status(self) -> str:
        """Human-readable status for UI display."""
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and _ensure_utc(self.expires_at) < utc_now():
            return "expired"
        return "active"


# =============================================================================
# API request models
# =============================================================================


_DEFAULT_SCOPES = ["chat", "session.delete", "session.list"]
# `bridge_identity` (v0.5) is NOT in DEFAULT — owner must explicitly grant
# it when minting a token for a trusted first-party integrator. Without
# this scope, the external API ignores any `metadata.user_id` the
# integrator might pass and falls back to the ephemeral path (defence
# against a token holder claiming arbitrary real user_ids).
_VALID_SCOPES = {
    "chat",
    "session.delete",
    "session.list",
    "usage.read",
    "bridge_identity",
}


class ApiKeyCreateRequest(BaseModel):
    """POST body for creating a new key."""

    name: str = Field(..., min_length=1, max_length=128)
    scopes: List[str] = Field(default_factory=lambda: list(_DEFAULT_SCOPES))
    expires_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class ApiKeyUpdateRequest(BaseModel):
    """PATCH body — every field optional. Absent fields untouched.
    Empty list for `scopes` is treated as "clear all scopes"; pass None to
    leave existing scopes alone.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=128)
    scopes: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


# =============================================================================
# API response models
# =============================================================================


class ApiKeyInfo(BaseModel):
    """Single key info — what owner sees in the list/detail views.
    Never includes the SHA256 hash or full plaintext token.
    """

    key_id: str
    name: str
    token_prefix: str
    agent_id: str
    owner_user_id: str
    scopes: List[str]
    status: str  # "active" | "expired" | "revoked"
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ApiKeyListResponse(BaseModel):
    """GET response — list of keys for an agent."""

    success: bool = True
    keys: List[ApiKeyInfo] = Field(default_factory=list)
    count: int = 0
    error: Optional[str] = None


class ApiKeyCreateResponse(BaseModel):
    """POST response — INCLUDES the plaintext token, **only this once**.

    Frontend MUST show this in a copy-and-save modal; refreshing the page
    or re-loading the key list does not bring it back. If the user loses
    it they must rotate (creates a new plaintext) or revoke (and create
    a fresh key).
    """

    success: bool = True
    key: Optional[ApiKeyInfo] = None
    plaintext_token: Optional[str] = Field(
        None,
        description=(
            "Full token, e.g. `nxk_apk_<key_id>_<random64>`. ONLY returned "
            "from this endpoint, ONLY at creation time. Never stored or "
            "retrievable thereafter."
        ),
    )
    error: Optional[str] = None


class ApiKeyResponse(BaseModel):
    """PATCH / DELETE response — does NOT include plaintext token."""

    success: bool = True
    key: Optional[ApiKeyInfo] = None
    error: Optional[str] = None


class ApiKeyRotateResponse(BaseModel):
    """POST /rotate response — same shape as Create (includes plaintext).

    Server-side, rotate is "revoke old key (with `grace_period_days`
    countdown set via expires_at) + create new key under the same
    semantic identity". Both keys are functional during the grace window;
    the old one stops working when its expires_at passes.
    """

    success: bool = True
    new_key: Optional[ApiKeyInfo] = None
    revoked_old_key_id: Optional[str] = None
    grace_until: Optional[str] = None
    plaintext_token: Optional[str] = None
    error: Optional[str] = None
