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
from pydantic import BaseModel

from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.repository.ban_audit_repository import (
    ACTION_REINSTATE,
    ACTION_SUSPEND,
    BanAuditRepository,
)
from xyz_agent_context.schema import UserStatus

router = APIRouter(prefix="/api/admin", tags=["admin"])

# The account states that count as "suspended" for the idempotency check.
# A suspend applied to an already non-transacting account is a no-op success.
_SUSPENDED_STATES = {
    UserStatus.BANNED.value,
    UserStatus.BLOCKED.value,
    UserStatus.DELETED.value,
}


class SuspendRequest(BaseModel):
    user_id: str
    reason: Optional[str] = None
    evidence_ref: Optional[str] = None
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


def _require_admin_secret(provided: str) -> None:
    """Gate the endpoint on the platform admin secret.

    No configured secret in cloud-grade deployments is a misconfiguration, not
    an open door — refuse rather than allow. A wrong / missing header is 403.
    """
    expected = (settings.admin_secret_key or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin secret not configured")
    if not provided or provided.strip() != expected:
        raise HTTPException(status_code=403, detail="invalid admin secret")


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
    _require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user_repo = UserRepository(db)
    user = await user_repo.get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    already = user.status.value in _SUSPENDED_STATES
    if not already:
        await user_repo.update_user(
            request.user_id, {"status": UserStatus.BANNED}
        )

    # Audit every call (including the idempotent no-op) so the trail records
    # who asked and when, even when the state did not change.
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_SUSPEND,
        reason=request.reason,
        evidence_ref=request.evidence_ref,
        actor=request.actor,
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
    _require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user_repo = UserRepository(db)
    user = await user_repo.get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    await user_repo.update_user(request.user_id, {"status": UserStatus.ACTIVE})
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_REINSTATE,
        actor=request.actor,
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
    _require_admin_secret(x_admin_secret)

    db = await get_db_client()
    user = await UserRepository(db).get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    return AccountStateResponse(user_id=user_id, status=user.status.value)
