"""
@file_name: gateway_session_key_schema.py
@author: Bin Liang
@date: 2026-07-23
@description: Data model for a per-run LiteLLM gateway session key ("会话票").

Free-tier agent runs no longer carry the shared master key. Each run mints one
short-lived gateway key that is injected into the agent subprocess env in its
place. This row is the ledger that lets us (a) revoke the key the moment the run
ends and (b) reap orphans left by a crash between mint and revoke.

Security note: we deliberately do NOT persist the raw, usable key secret. The
key is minted with ``key_alias == run_id`` (unique per run), and revocation goes
by that alias — so a leak of this table cannot be replayed against the gateway.
``key_hash`` is LiteLLM's non-secret token hash, kept only for audit/reconcile.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class GatewaySessionKeyStatus(str, Enum):
    ACTIVE = "active"      # minted, backing a live (or presumed-live) run
    REVOKED = "revoked"    # deleted at the gateway; no longer usable


class GatewaySessionKey(BaseModel):
    # run_id doubles as the gateway key_alias — the only handle needed to revoke.
    run_id: str
    user_id: str
    agent_id: Optional[str] = None
    # LiteLLM's returned token hash (NOT the secret). Audit/reconcile only.
    key_hash: Optional[str] = None
    status: GatewaySessionKeyStatus = GatewaySessionKeyStatus.ACTIVE
    created_at: datetime
    revoked_at: Optional[datetime] = None
    # Set once the run's LiteLLM SpendLogs were summed and deducted from the
    # user's free-tier quota. NULL = not yet metered (idempotency guard).
    metered_at: Optional[datetime] = None
