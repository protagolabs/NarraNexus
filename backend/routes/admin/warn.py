"""
@file_name: warn.py
@author: Bin Liang
@date: 2026-08-19
@description: Admin-only sensitive-operation user warning.

The progressive-response layer between scoring and hard enforcement: when the
sentinel detects a suspicious-but-unproven operation (a soft signal / scoring
rule such as bare tunnel contact), it POSTs here to push ONE generic warning to
the user. Gated on the platform ``admin_secret_key`` via ``X-Admin-Secret`` —
the SAME lock as suspend/reinstate; an executor or an agent has no such secret.

Security shape (do not weaken):
  * The user-facing wording is a FIXED generic constant (``SENSITIVE_OP_WARNING``)
    written here regardless of any input, so the detection rule / threshold /
    evidence can never leak to the user. The caller's ``category`` is OPAQUE and
    goes ONLY into the ban_audit trail, never into the user notification.
  * Best-effort dedup within ``_DEDUP_WINDOW_SEC``: a recent ``abuse_warning``
    for the same user short-circuits to ``already=True`` without a second row.
    This collapses a SEQUENTIAL retry (the caller re-POSTs after seeing/ not
    seeing a response). It is a read-then-write with no unique constraint behind
    it, so it is NOT atomic: two truly concurrent POSTs can both pass the check
    and both insert. That is an accepted cost here — the worst case is one
    duplicate generic warning notification, which is harmless (unlike a
    duplicate enforcement row); we do not pay for a unique index to prevent it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

# Re-exported so the admin secret can be overridden per-test via ``mod.settings``
# (this module's namespace); the shared ``require_admin_secret`` helper reads the
# same ``settings`` singleton object.
from xyz_agent_context.settings import settings  # noqa: F401
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now
from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.repository.ban_audit_repository import ACTION_WARN, BanAuditRepository

from ._admin_secret import require_admin_secret

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Fixed, generic, rule-agnostic wording. English per 铁律 #1 (no non-English
# strings in code); the frontend bell may localise on ``code``.
SENSITIVE_OP_WARNING = (
    "We detected activity on your account that may violate our Terms of Use "
    "(for example, attempting to transmit credentials externally or accessing "
    "sensitive environment data). Please stop immediately. Continued activity "
    "of this kind may result in your account being restricted or suspended."
)

# Idempotency window: a second warn for the same user inside this window is a
# no-op success. 6h matches the sentinel-side de-dup window; kept here so the
# endpoint is independently idempotent against retries.
_DEDUP_WINDOW_SEC = 6 * 3600


def _as_utc(value) -> Optional[datetime]:
    """Coerce a stored ``created_at`` to a tz-aware UTC datetime.

    The DB layer hands back a ``datetime`` on some backends and the ISO string
    SQLite stores on others; a naive value is assumed UTC (the storage
    convention, see ``utils/timezone``). Returns None for anything unusable so
    an unparseable row is simply ignored by the dedup scan (fail-safe: at worst
    a duplicate notification, never a crash).
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.rstrip("Z"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class WarnRequest(BaseModel):
    user_id: str
    # OPAQUE, audit-only. Never reaches the user notification. Bounded to keep a
    # stray payload out of the audit row.
    category: Optional[str] = Field(default=None, max_length=4096)
    actor: Optional[str] = None


class WarnResponse(BaseModel):
    warned: bool
    already: bool


@router.post("/warn-user", response_model=WarnResponse)
async def warn_user(
    request: WarnRequest,
    x_admin_secret: str = Header(default=""),
) -> WarnResponse:
    require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user = await UserRepository(db).get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    # Idempotency: a recent warning short-circuits without a second row. We only
    # need the newest row's timestamp to decide, so read just that one column of
    # the single latest row — not every abuse_warning row in full. The payload
    # column is MEDIUMTEXT, so a full-row scan of a chatty user's history would
    # drag the whole notification body back for nothing.
    existing = await db.get(
        "user_notifications",
        {"user_id": request.user_id, "kind": "abuse_warning"},
        limit=1,
        order_by="created_at DESC",
        fields=["created_at"],
    )
    now = utc_now()
    cutoff = now - timedelta(seconds=_DEDUP_WINDOW_SEC)
    recent = [
        r
        for r in (existing or [])
        if (ts := _as_utc(r.get("created_at"))) is not None and ts >= cutoff
    ]
    if recent:
        logger.info(
            f"[warn-user] user={request.user_id} already (within dedup window)"
        )
        return WarnResponse(warned=True, already=True)

    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    await db.insert(
        "user_notifications",
        {
            "user_id": request.user_id,
            "kind": "abuse_warning",
            "payload": json.dumps(
                {
                    "code": "sensitive_operation_warning",
                    "message": SENSITIVE_OP_WARNING,
                },
                ensure_ascii=False,
            ),
            "severity": "warning",
            "read_at": None,
            "created_at": now_iso,
        },
    )
    # Audit: category is OPAQUE (recorded verbatim in ``reason``), never shown to
    # the user. The precise rule/evidence lives in sentinel's incident ledger.
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_WARN,
        reason=request.category,
        actor=request.actor,
        prev_status=user.status.value,
    )
    logger.info(
        f"[warn-user] user={request.user_id} category={request.category or '-'} "
        f"actor={request.actor or '-'}"
    )
    return WarnResponse(warned=True, already=False)
