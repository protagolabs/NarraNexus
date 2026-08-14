"""
@file_name: suspend.py
@author: Bin Liang
@date: 2026-08-13
@description: Admin-only account-suspension mechanism.

A generic, reusable switch over a user's account state, driven by a private
caller. This module holds NO policy: it does not decide who should be
suspended or why. It exposes three self-credentialed operations —

    POST /api/admin/suspend           set account state to suspended
    POST /api/admin/reinstate         return account state to active
    GET  /api/admin/account-state/{user_id}   read current account state

— each gated on the platform ``admin_secret_key`` via an ``X-Admin-Secret``
header (never a user JWT, never open), exactly like migrate-identity. The
``reason`` and ``evidence_ref`` fields are OPAQUE free text recorded verbatim
in the ``ban_audit`` trail; this layer never interprets or categorises them.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

# Re-exported so the admin secret can be overridden per-test via
# ``mod.settings`` (this module's namespace); the shared ``require_admin_secret``
# helper reads the same ``settings`` singleton object.
from xyz_agent_context.settings import settings  # noqa: F401
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.repository.ban_audit_repository import (
    ACTION_REINSTATE,
    ACTION_SUSPEND,
    BanAuditRepository,
)
from xyz_agent_context.schema import NON_TRANSACTING_USER_STATUSES, UserStatus

from ._admin_secret import require_admin_secret

router = APIRouter(prefix="/api/admin", tags=["admin"])

# The account states that count as "suspended" for the idempotency check: the
# shared non-transacting set (single source of truth with the auth middleware /
# WS gate / login gate). A suspend applied to an already non-transacting account
# is a no-op success.
_SUSPENDED_STATES = NON_TRANSACTING_USER_STATUSES


class SuspendRequest(BaseModel):
    user_id: str
    # reason / evidence_ref are OPAQUE free text recorded verbatim; bounded only
    # to keep a stray unbounded payload out of the audit row (the backing column
    # is MEDIUMTEXT — the bound is a request-side guardrail, not a storage cap).
    reason: Optional[str] = Field(default=None, max_length=4096)
    evidence_ref: Optional[str] = Field(default=None, max_length=4096)
    actor: Optional[str] = None


class SuspendResponse(BaseModel):
    suspended: bool
    already: bool


class ReinstateRequest(BaseModel):
    user_id: str
    actor: Optional[str] = None


class ReinstateResponse(BaseModel):
    reinstated: bool


class AccountStateResponse(BaseModel):
    user_id: str
    status: str


def _invalidate_cache(user_id: str) -> None:
    """Best-effort drop of the middleware's cached account state.

    Imported lazily so this route module never pulls the auth middleware in at
    import time (and so tests can exercise the route without the full app).
    """
    try:
        from backend.auth import invalidate_account_state

        invalidate_account_state(user_id)
    except Exception as e:  # noqa: BLE001 — cache invalidation is best-effort
        logger.warning(
            f"[suspend] cache invalidation skipped for {user_id}: "
            f"{type(e).__name__}: {e}"
        )


@router.post("/suspend", response_model=SuspendResponse)
async def suspend_account(
    request: SuspendRequest,
    x_admin_secret: str = Header(default=""),
) -> SuspendResponse:
    require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user_repo = UserRepository(db)
    user = await user_repo.get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    prev_status = user.status.value
    already = prev_status in _SUSPENDED_STATES
    if not already:
        await user_repo.update_user(
            request.user_id, {"status": UserStatus.BANNED}
        )

    # Audit every call (including the idempotent no-op) so the trail records
    # who asked and when, even when the state did not change. prev_status is the
    # state the account was in before this call.
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_SUSPEND,
        reason=request.reason,
        evidence_ref=request.evidence_ref,
        actor=request.actor,
        prev_status=prev_status,
    )
    _invalidate_cache(request.user_id)

    logger.info(
        f"[suspend] user={request.user_id} already={already} "
        f"actor={request.actor or '-'}"
    )
    return SuspendResponse(suspended=True, already=already)


@router.post("/reinstate", response_model=ReinstateResponse)
async def reinstate_account(
    request: ReinstateRequest,
    x_admin_secret: str = Header(default=""),
) -> ReinstateResponse:
    require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user_repo = UserRepository(db)
    user = await user_repo.get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    prev_status = user.status.value

    # Only reinstate an account THIS mechanism suspended, i.e. one whose state
    # is exactly BANNED. `blocked` / `deleted` are pre-existing terminal states
    # this switch never set and must not silently revive — reinstating one would
    # be a cross-mechanism side effect. Record the attempt in the audit trail
    # either way (prev_status shows what it was), then refuse.
    if prev_status != UserStatus.BANNED.value:
        await BanAuditRepository(db).record(
            request.user_id,
            ACTION_REINSTATE,
            actor=request.actor,
            prev_status=prev_status,
        )
        logger.info(
            f"[reinstate] refused (not banned) user={request.user_id} "
            f"status={prev_status} actor={request.actor or '-'}"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "reinstated": False,
                "not_suspended_by_this_mechanism": True,
                "status": prev_status,
            },
        )

    await user_repo.update_user(request.user_id, {"status": UserStatus.ACTIVE})
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_REINSTATE,
        actor=request.actor,
        prev_status=prev_status,
    )
    _invalidate_cache(request.user_id)

    logger.info(
        f"[reinstate] user={request.user_id} actor={request.actor or '-'}"
    )
    return ReinstateResponse(reinstated=True)


@router.get("/account-state/{user_id}", response_model=AccountStateResponse)
async def get_account_state(
    user_id: str,
    x_admin_secret: str = Header(default=""),
) -> AccountStateResponse:
    require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user = await UserRepository(db).get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    return AccountStateResponse(user_id=user_id, status=user.status.value)
