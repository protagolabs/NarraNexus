"""
@file_name: quota.py
@author: Bin Liang
@date: 2026-04-16
@description: User-facing quota query endpoint.

Three explicit response shapes so the frontend does not have to infer
"is the feature on":
  - {enabled: false}                          — local mode / env not set
  - {enabled: true, status: "uninitialized"}  — cloud, user has no row yet
  - {enabled: true, status: "active"|..., …}  — full budget breakdown
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.auth import _is_cloud_mode
from xyz_agent_context.agent_framework.provider_resolver import ProviderResolver
from xyz_agent_context.agent_framework.user_provider_service import UserProviderService
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter(prefix="/api/quota", tags=["quota"])


async def _free_tier_lock(user_id: str, sys_svc, quota_svc) -> dict:
    """Is a run for ``user_id`` right now pinned to the SYSTEM free tier, and if
    so which model does it actually run?

    While the free tier has budget the runtime ignores the user's own
    agent/helper slots (see ``ProviderResolver.resolve`` SYSTEM_OK branch) — so
    editing the global Model Defaults (or a per-agent override) silently no-ops
    until the free tier is spent. The settings UIs read this to render an honest
    "changes take effect once your free quota is used up" banner. Verdict comes
    from the single-source predicate ``ProviderResolver.is_free_tier_active``;
    the model is the fixed system agent model surfaced while locked.
    """
    resolver = ProviderResolver(
        user_provider_svc=UserProviderService(await get_db_client()),
        system_provider_svc=sys_svc,
        quota_svc=quota_svc,
    )
    if not await resolver.is_free_tier_active(user_id):
        return {"active": False, "model": None}
    return {"active": True, "model": sys_svc.get_config().slots["agent"].model}


def _quota_to_dict(q) -> dict:
    return {
        "enabled": True,
        "status": q.status.value,
        "remaining_input_tokens": q.remaining_input,
        "remaining_output_tokens": q.remaining_output,
        "initial_input_tokens": q.initial_input_tokens,
        "initial_output_tokens": q.initial_output_tokens,
        "granted_input_tokens": q.granted_input_tokens,
        "granted_output_tokens": q.granted_output_tokens,
        "used_input_tokens": q.used_input_tokens,
        "used_output_tokens": q.used_output_tokens,
        "prefer_system_override": q.prefer_system_override,
    }


@router.get("/me")
async def get_my_quota(request: Request) -> dict:
    # Local mode: feature is strictly off; do not consult any service.
    if not _is_cloud_mode():
        return {"enabled": False}

    sys_svc = getattr(request.app.state, "system_provider", None)
    quota_svc = getattr(request.app.state, "quota_service", None)
    if sys_svc is None or quota_svc is None:
        # Services not wired (should only happen pre-lifespan in tests).
        return {"enabled": False}

    if not sys_svc.is_enabled():
        return {"enabled": False}

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    free_tier = await _free_tier_lock(user_id, sys_svc, quota_svc)

    q = await quota_svc.get(user_id)
    if q is None:
        return {"enabled": True, "status": "uninitialized", "free_tier": free_tier}

    return {**_quota_to_dict(q), "free_tier": free_tier}


# PATCH /me/preference was removed 2026-07-18: "free tier first" is platform
# behavior now, not a user preference — the resolver always draws the free
# tier while it has budget and falls through to the user's own provider when
# exhausted (see provider_resolver). The prefer_system_override column
# survives as the exhaustion-notice dedup latch only.
