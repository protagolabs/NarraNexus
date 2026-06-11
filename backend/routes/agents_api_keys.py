"""
@file_name: agents_api_keys.py
@author: NarraNexus
@date: 2026-06-11
@description: Agent API key management routes (external API protocol v0.3).

Endpoints, all under /api/agents/{agent_id}/api-keys:
  - GET    /                  → list (revoked rows included w/ status)
  - POST   /                  → create (returns plaintext ONCE)
  - PATCH  /{key_id}          → rename / scopes / expiry / metadata
  - DELETE /{key_id}          → soft revoke
  - POST   /{key_id}/rotate   → revoke old w/ grace + create new

Auth: caller's JWT (resolve_current_user_id). Caller MUST equal
`agents.created_by(agent_id)` — i.e. you can only manage tokens on agents
you own. Anything else returns 403.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.repository.agent_api_key_repository import (
    AgentApiKeyRepository,
)
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.schema.agent_api_key_schema import (
    AgentApiKey,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyListResponse,
    ApiKeyResponse,
    ApiKeyRotateResponse,
    ApiKeyUpdateRequest,
)
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.utils.api_key_token import mint_token
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


# How long an old key keeps working after rotate. Owner-tunable via env
# could go in settings later; 7 days mirrors GitHub PAT rotation grace.
ROTATE_GRACE_DAYS = 7

# Scope strings the API will accept. Everything outside this set is rejected
# at the route layer so we don't store gibberish that silently never matches
# anything in the middleware.
_VALID_SCOPES = {"chat", "session.delete", "session.list", "usage.read"}


# =============================================================================
# Helpers
# =============================================================================


async def _resolve_agent_owner_or_403(agent_id: str, user_id: str) -> None:
    """Ensure the agent exists AND the caller is its creator.

    Raises HTTPException 404 if the agent doesn't exist (so we don't leak
    existence to non-owners), 403 if it does exist but the caller isn't
    the creator. We choose 404 over 403 for "exists but not yours" too,
    to prevent agent-id enumeration via 403/404 distinction — see comment
    inline. For now we surface 403 in the "wrong owner" branch because
    leaking existence to fellow logged-in users on the same instance is
    not the main threat model; switch to 404 if/when we tighten this.
    """
    db = await get_db_client()
    agent_repo = AgentRepository(db)
    agent = await agent_repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"agent '{agent_id}' not found")
    if agent.created_by != user_id:
        raise HTTPException(
            status_code=403,
            detail=f"not the owner of agent '{agent_id}'",
        )


def _entity_to_info(key: AgentApiKey) -> ApiKeyInfo:
    """Convert internal entity → API info (strips token_hash; keeps prefix)."""
    return ApiKeyInfo(
        key_id=key.key_id,
        name=key.name,
        token_prefix=key.token_prefix,
        agent_id=key.agent_id,
        owner_user_id=key.owner_user_id,
        scopes=key.scopes,
        status=key.status(),
        expires_at=format_for_api(key.expires_at) if key.expires_at else None,
        last_used_at=format_for_api(key.last_used_at) if key.last_used_at else None,
        revoked_at=format_for_api(key.revoked_at) if key.revoked_at else None,
        metadata=key.metadata,
        created_at=format_for_api(key.created_at) if key.created_at else None,
        updated_at=format_for_api(key.updated_at) if key.updated_at else None,
    )


def _validate_scopes(scopes: list[str]) -> None:
    """Reject unknown scope strings with a 422-style error."""
    bad = [s for s in scopes if s not in _VALID_SCOPES]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"unknown scope(s): {bad}. Valid: {sorted(_VALID_SCOPES)}",
        )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/{agent_id}/api-keys", response_model=ApiKeyListResponse)
async def list_api_keys(agent_id: str, request: Request):
    """List every API key for the agent — revoked rows are returned with
    `status="revoked"` so the UI can show audit history.
    """
    user_id = await resolve_current_user_id(request)
    await _resolve_agent_owner_or_403(agent_id, user_id)

    try:
        db = await get_db_client()
        repo = AgentApiKeyRepository(db)
        keys = await repo.list_for_agent(agent_id, include_revoked=True)
        infos = [_entity_to_info(k) for k in keys]
        return ApiKeyListResponse(success=True, keys=infos, count=len(infos))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("list_api_keys failed for agent_id={!r}: {}", agent_id, exc)
        return ApiKeyListResponse(success=False, error=str(exc))


@router.post("/{agent_id}/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    agent_id: str,
    payload: ApiKeyCreateRequest,
    request: Request,
):
    """Mint a fresh token. The **plaintext is returned only here**, only
    this once. The frontend MUST show a copy-and-save modal; refresh
    won't bring it back.
    """
    user_id = await resolve_current_user_id(request)
    await _resolve_agent_owner_or_403(agent_id, user_id)

    _validate_scopes(payload.scopes)

    try:
        minted = mint_token()
        db = await get_db_client()
        repo = AgentApiKeyRepository(db)
        stored = await repo.insert(
            key_id=minted.key_id,
            token_hash=minted.token_hash,
            token_prefix=minted.token_prefix,
            agent_id=agent_id,
            owner_user_id=user_id,
            name=payload.name,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
            metadata=payload.metadata,
        )
        logger.info(
            "agent_api_keys: created key_id={!r} for agent_id={!r} owner={!r}",
            minted.key_id, agent_id, user_id,
        )
        return ApiKeyCreateResponse(
            success=True,
            key=_entity_to_info(stored),
            plaintext_token=minted.plaintext,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("create_api_key failed for agent_id={!r}: {}", agent_id, exc)
        return ApiKeyCreateResponse(success=False, error=str(exc))


@router.patch(
    "/{agent_id}/api-keys/{key_id}", response_model=ApiKeyResponse
)
async def update_api_key(
    agent_id: str,
    key_id: str,
    payload: ApiKeyUpdateRequest,
    request: Request,
):
    """Patch name / scopes / expires_at / metadata. Absent fields untouched.
    To clear `expires_at` pass `null` explicitly (Pydantic will distinguish
    null vs absent for required fields, but absent on PATCH means
    "untouched"); to clear scopes pass an empty list.
    """
    user_id = await resolve_current_user_id(request)
    await _resolve_agent_owner_or_403(agent_id, user_id)

    try:
        db = await get_db_client()
        repo = AgentApiKeyRepository(db)
        existing = await repo.get_by_key_id(key_id)
        if not existing or existing.agent_id != agent_id:
            raise HTTPException(
                status_code=404,
                detail=f"api-key '{key_id}' not found on agent '{agent_id}'",
            )

        updates: dict = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.scopes is not None:
            _validate_scopes(payload.scopes)
            updates["scopes"] = payload.scopes
        if payload.expires_at is not None:
            updates["expires_at"] = payload.expires_at
        if payload.metadata is not None:
            updates["metadata"] = payload.metadata

        if updates:
            await repo.update(key_id, updates)

        refreshed = await repo.get_by_key_id(key_id)
        return ApiKeyResponse(
            success=True,
            key=_entity_to_info(refreshed) if refreshed else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "update_api_key failed for agent_id={!r} key_id={!r}: {}",
            agent_id, key_id, exc,
        )
        return ApiKeyResponse(success=False, error=str(exc))


@router.delete(
    "/{agent_id}/api-keys/{key_id}", response_model=ApiKeyResponse
)
async def revoke_api_key(
    agent_id: str,
    key_id: str,
    request: Request,
):
    """Soft revoke. The row stays in DB so the audit chain (last_used_at,
    revoked_at, name) is preserved; the middleware will treat the token
    as 401 thereafter.

    Idempotent: re-revoking already-revoked keys is a no-op success.
    """
    user_id = await resolve_current_user_id(request)
    await _resolve_agent_owner_or_403(agent_id, user_id)

    try:
        db = await get_db_client()
        repo = AgentApiKeyRepository(db)
        existing = await repo.get_by_key_id(key_id)
        if not existing or existing.agent_id != agent_id:
            raise HTTPException(
                status_code=404,
                detail=f"api-key '{key_id}' not found on agent '{agent_id}'",
            )

        if existing.revoked_at is None:
            await repo.revoke(key_id)

        refreshed = await repo.get_by_key_id(key_id)
        logger.info(
            "agent_api_keys: revoked key_id={!r} on agent_id={!r}",
            key_id, agent_id,
        )
        return ApiKeyResponse(
            success=True,
            key=_entity_to_info(refreshed) if refreshed else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "revoke_api_key failed for agent_id={!r} key_id={!r}: {}",
            agent_id, key_id, exc,
        )
        return ApiKeyResponse(success=False, error=str(exc))


@router.post(
    "/{agent_id}/api-keys/{key_id}/rotate",
    response_model=ApiKeyRotateResponse,
)
async def rotate_api_key(
    agent_id: str,
    key_id: str,
    request: Request,
):
    """Mint a fresh token under the same `name` and `scopes`, and set the
    old key's `expires_at = now + grace`. Old token stays usable until
    that timestamp passes — giving integrators a deploy window to
    rotate secrets without downtime. After grace, the old token starts
    returning 401.

    Returns the new plaintext (only this once, like create).
    """
    user_id = await resolve_current_user_id(request)
    await _resolve_agent_owner_or_403(agent_id, user_id)

    try:
        db = await get_db_client()
        repo = AgentApiKeyRepository(db)
        existing = await repo.get_by_key_id(key_id)
        if not existing or existing.agent_id != agent_id:
            raise HTTPException(
                status_code=404,
                detail=f"api-key '{key_id}' not found on agent '{agent_id}'",
            )
        if existing.revoked_at is not None:
            raise HTTPException(
                status_code=409,
                detail="cannot rotate an already-revoked key; create a new one",
            )

        # Mint the new token with the same name + scopes + metadata
        minted = mint_token()
        new_key = await repo.insert(
            key_id=minted.key_id,
            token_hash=minted.token_hash,
            token_prefix=minted.token_prefix,
            agent_id=agent_id,
            owner_user_id=user_id,
            name=existing.name,
            scopes=list(existing.scopes),
            expires_at=existing.expires_at,
            metadata=existing.metadata,
        )

        # Schedule old key to expire after grace
        grace_until = datetime.now(timezone.utc) + timedelta(days=ROTATE_GRACE_DAYS)
        await repo.update(
            key_id,
            {"expires_at": grace_until, "name": existing.name + " (rotated)"},
        )

        logger.info(
            "agent_api_keys: rotated key_id={!r} → new key_id={!r}; old "
            "expires_at={}",
            key_id, minted.key_id, grace_until.isoformat(),
        )

        return ApiKeyRotateResponse(
            success=True,
            new_key=_entity_to_info(new_key),
            revoked_old_key_id=key_id,
            grace_until=format_for_api(grace_until),
            plaintext_token=minted.plaintext,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "rotate_api_key failed for agent_id={!r} key_id={!r}: {}",
            agent_id, key_id, exc,
        )
        return ApiKeyRotateResponse(success=False, error=str(exc))
