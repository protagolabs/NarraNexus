"""
@file_name: gateway_key_misuse.py
@author: Bin Liang
@date: 2026-08-19
@description: Internal admin endpoint that records an authoritative gateway-key
misuse event — the SOLE writer of gateway_key_misuse.

    POST /api/admin/gateway-key-misuse    record one gateway-key misuse event

Neutral / opaque by design: it stores a ``user_id`` that the caller already
AUTHORITATIVELY reverse-resolved for the offending key (the gateway is the
authority on which identity the key is bound to). This endpoint parses NO
free-form text for attribution — it records only the fields it is handed. Gated
on ``X-Admin-Secret`` (the same lock as suspend / migrate-identity), so only the
internal server-to-server path can write here; executor / agent hold no such
credential.

The written row is the payload, not an advisory note: the security monitor reads
gateway_key_misuse read-only to drive its response ladder. So the write goes
through the repository layer ([[gateway_key_misuse_repository]]) and a failure
surfaces (the row was not persisted), rather than being swallowed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header
from loguru import logger
from pydantic import BaseModel

# Re-exported so the admin secret can be overridden per-test via ``mod.settings``
# (this module's namespace); the shared ``require_admin_secret`` helper reads the
# same ``settings`` singleton object.
from xyz_agent_context.settings import settings  # noqa: F401
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.repository.gateway_key_misuse_repository import (
    GatewayKeyMisuseRepository,
)

from ._admin_secret import require_admin_secret

router = APIRouter(prefix="/api/admin", tags=["admin"])


class GatewayKeyMisuseRequest(BaseModel):
    # Authoritative reverse-resolved id, or None for an alert-only row. Every
    # field is what the caller resolved/observed; this endpoint adds no
    # interpretation. Unknown fields are ignored (pydantic drops them) — an
    # attacker cannot smuggle an alternative attribution in.
    #
    # NO length validators on any field: the values here are attacker-influenced
    # (a caller forwards what it observed, e.g. the presented caller_ua). A
    # max_length would turn an over-long field into a 422 — i.e. a DROPPED event,
    # which is a missed enforcement. Every event MUST land, so over-long fields
    # are CLIPPED server-side (see ``_clip``) instead of rejected.
    user_id: Optional[str] = None
    run_id: Optional[str] = None
    key_hash: Optional[str] = None
    caller_ip: Optional[str] = None
    caller_ua: Optional[str] = None
    model: Optional[str] = None
    # Authoritative time the misuse occurred (caller-supplied). When present it
    # is the idempotency anchor: a retry of the same event carries the same
    # (key_hash, hit_at) and collapses to one row (the UNIQUE index). Omitted →
    # the column default (insert time) applies and no dedup is possible.
    hit_at: Optional[str] = None


class GatewayKeyMisuseResponse(BaseModel):
    id: int
    recorded: bool


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Truncate an attacker-influenced string field to its column width.

    None passes through untouched. A value longer than the column would be
    rejected by MySQL on write (a str too long for its VARCHAR is a write
    error, i.e. a lost row); every misuse event MUST be recorded — a dropped
    row is a missed enforcement — so we clip rather than reject. The leading
    bytes still carry the signal a human or the monitor needs.
    """
    if value is None:
        return None
    return value[:limit]


@router.post("/gateway-key-misuse", response_model=GatewayKeyMisuseResponse)
async def record_gateway_key_misuse(
    request: GatewayKeyMisuseRequest,
    x_admin_secret: str = Header(default=""),
) -> GatewayKeyMisuseResponse:
    require_admin_secret(x_admin_secret)

    # Clip every attacker-influenced field to its column width. An event must
    # ALWAYS land: never 422/500 a real misuse event away just because a field
    # is over-long (dropping one row = missing one enforcement).
    run_id = _clip(request.run_id, 128)
    key_hash = _clip(request.key_hash, 256)
    caller_ip = _clip(request.caller_ip, 64)
    caller_ua = _clip(request.caller_ua, 256)
    model = _clip(request.model, 128)

    # user_id is special: it drives enforcement, so we NEVER truncate it — a
    # clipped id could collide with a DIFFERENT real user (a mis-attributed
    # ban). An over-long id is treated as "reverse-resolution failed" and
    # recorded as an alert-only row (user_id=NULL, disposition stays 'pending')
    # for a human to triage; the event still lands, we just refuse to guess who.
    user_id = request.user_id
    if user_id is not None and len(user_id) > 128:
        logger.error(
            "[gateway-key-misuse] user_id over 128 chars "
            f"(len={len(user_id)}) — recording alert-only row with NULL id"
        )
        user_id = None

    db = await get_db_client()
    row_id = await GatewayKeyMisuseRepository(db).record(
        user_id=user_id,
        run_id=run_id,
        key_hash=key_hash,
        caller_ip=caller_ip,
        caller_ua=caller_ua,
        model=model,
        hit_at=request.hit_at,
    )
    return GatewayKeyMisuseResponse(id=row_id, recorded=True)
