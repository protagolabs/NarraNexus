"""
@file_name: channel_credentials.py
@author:
@date: 2026-08-10
@description: Backend HTTP twin of the ChannelCredentialStore seam (blueprint
P2, #2 "mcp zero db creds", PR-A).

This is the ONLY endpoint in the codebase that returns a raw channel secret
(Discord ``bot_token``, and each further channel as its PR lands), so it is
gated on the PROVEN nx-service caller identity via ``assert_owned`` — never a
self-declared user id. ``auth_middleware`` has already verified the executor's
broker-signed Ed25519 identity and set ``request.state.user_id`` before this
handler runs (fail-closed: forged/expired/mismatched -> 401 before reaching
here); this route only adds the OWNER check on top of that proven identity.

Endpoints (mounted under /api/agents by agents/core.py):
  GET /{agent_id}/channels/{channel}/credential  -> raw cred dict | {"bound": false}
  GET /{agent_id}/channels/name                  -> {"agent_name": str}

``channel`` is validated against an allowlist (PR-A: only "discord") -> 404
for anything else, so this endpoint can never be used to probe for channels
that don't have a credential manager wired yet.

Never logs the secret — the credential dataclasses' ``to_raw_dict()`` is the
only place the raw fields leave the manager, and this handler passes it
straight through to the response body without touching individual fields.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

# Delegate the actual db access to the seam's DirectStore so this HTTP twin and
# the in-process path are ONE implementation (same manager dispatch, same raw
# serialisation) — adding a channel is one registry line in channel_store.py,
# never an edit here. Backend importing the agent package is the allowed
# one-way hop (铁律 #21).
from xyz_agent_context.module.data_access.channel_store import (
    SUPPORTED_CHANNELS,
    DirectStore as ChannelDirectStore,
)

# Ownership gate (backend/routes/_ownership.py): agent_id is attacker-
# controlled input — without the owner check, any nx-service caller could
# read ANY agent's channel secret (cross-tenant credential theft). Local mode
# (no JWT identity) does not enforce; see the helper's security-posture
# docstring before relying on it.
from backend.routes._ownership import assert_owned

# Service-identity gate: this endpoint returns a RAW secret, so it is
# restricted to the executor→mcp service path (an nx-agent bearer whose
# broker-signed identity auth_middleware already proved). A logged-in USER's
# session JWT can pass assert_owned for its own agent, but the panel already
# masks credentials on purpose — letting a browser session pull the raw token
# back through the API would re-widen exactly that surface (an XSS could
# exfiltrate every bound bot token). So require the service bearer here; the
# owner check then runs on the identity it proved.
from backend.auth import _is_nx_service_bearer

router = APIRouter()


def _require_service_caller(request: Request) -> None:
    if not _is_nx_service_bearer(request.headers.get("authorization") or ""):
        raise HTTPException(
            status_code=403,
            detail="raw channel credentials are only served to the nx-service path",
        )


@router.get("/{agent_id}/channels/{channel}/credential")
async def get_channel_credential(request: Request, agent_id: str, channel: str) -> dict:
    """Raw credential for ``channel`` (service + owner gated), or
    ``{"bound": false}`` when the agent has no binding. Delegates to
    ChannelCredentialStore.DirectStore so this HTTP twin returns byte-identical
    payloads to the in-process seam a local caller would get."""
    if channel not in SUPPORTED_CHANNELS:
        raise HTTPException(status_code=404, detail=f"unknown channel: {channel}")
    _require_service_caller(request)
    await assert_owned(request, agent_id)

    raw = await ChannelDirectStore().get_credential(channel, agent_id)
    return raw if raw is not None else {"bound": False}


@router.get("/{agent_id}/channels/name")
async def get_agent_channel_name(request: Request, agent_id: str) -> dict:
    """The agent's human-readable name (service + owner gated) — the HTTP twin
    of ChannelCredentialStore.DirectStore.get_agent_name, falling back to the
    id itself when the name is missing."""
    _require_service_caller(request)
    await assert_owned(request, agent_id)
    return {"agent_name": await ChannelDirectStore().get_agent_name(agent_id)}


@router.get("/{agent_id}/channels/owner")
async def get_agent_channel_owner(request: Request, agent_id: str) -> dict:
    """The agent's owner (created_by) user id (service + owner gated) — the HTTP
    twin of ChannelCredentialStore.DirectStore.get_agent_owner. NarraMessenger's
    media send + CLI workspace resolution need it. Since assert_owned already
    proved the caller owns the agent, this necessarily equals the caller's own
    identity, but it is served explicitly so the tool need not re-derive it."""
    _require_service_caller(request)
    await assert_owned(request, agent_id)
    return {"owner_user_id": await ChannelDirectStore().get_agent_owner(agent_id)}
