"""
@file_name: lark.py
@date: 2026-04-10
@description: Backend API routes for Lark/Feishu bot binding, auth, and management.

Endpoints (the Lark-specific OAuth flow only — bind / credential / unbind /
set-active are the generic ``/api/channels/lark/…`` routes since batch 4d.3):
  POST   /api/lark/auth/login    — Initiate OAuth login (returns auth URL)
  POST   /api/lark/auth/complete — Complete OAuth with device code
  GET    /api/lark/auth/status   — Check login status
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from loguru import logger

from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredentialManager,
)
from xyz_agent_context.module.lark_module._lark_service import determine_auth_status
from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient


# One canonical owner check (backend/routes/_ownership.py); module-level
# alias keeps the historical local name at its ~per-route call sites. No
# import cycle: this subpackage never gets imported back from _ownership.
from backend.routes._ownership import check_owned as _verify_agent_ownership

router = APIRouter()
_cli = LarkCLIClient()

# Pattern for safe agent_id values (alphanumeric + underscore + hyphen)
_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"
# Device code pattern (alphanumeric + common separators)
_DEVICE_CODE_PATTERN = r"^[a-zA-Z0-9_\-\.]{1,256}$"


# =========================================================================
# Request / Response schemas
# =========================================================================

class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class AuthCompleteRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)
    device_code: str = Field(min_length=1, max_length=256, pattern=_DEVICE_CODE_PATTERN)


# =========================================================================
# Helper
# =========================================================================

async def _get_db():
    """Get database client via factory (same pattern as other routes)."""
    from xyz_agent_context.utils.db.db_factory import get_db_client
    return await get_db_client()


# =========================================================================
# Endpoints
# =========================================================================

@router.post("/auth/login")
async def lark_auth_login(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Initiate OAuth login. Returns auth URL for browser authorization."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    # V2: use workspace-based runner
    result = await _cli._run_with_agent_id(
        ["auth", "login", "--recommend", "--json", "--no-wait"],
        agent_id=body.agent_id,
        timeout=60.0,
    )
    return result


@router.post("/auth/complete")
async def lark_auth_complete(request: Request, body: AuthCompleteRequest) -> dict[str, Any]:
    """Complete OAuth login with device code from a previous --no-wait call."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    # V2: use workspace-based runner
    result = await _cli._run_with_agent_id(
        ["auth", "login", "--device-code", body.device_code, "--json"],
        agent_id=body.agent_id,
        timeout=60.0,
    )

    # Update auth status on success
    if result.get("success"):
        from xyz_agent_context.module.lark_module._lark_credential_manager import AUTH_STATUS_USER_LOGGED_IN
        await mgr.update_auth_status(body.agent_id, AUTH_STATUS_USER_LOGGED_IN)

        # Try to get bot name via bot-info API (bot identity has no "self"
        # user concept, so `+get-user --as bot` fails without --user-id).
        bot_info = await _cli._run_with_agent_id(
            ["api", "GET", "/open-apis/bot/v3/info", "--as", "bot"],
            agent_id=body.agent_id,
        )
        if bot_info.get("success"):
            bdata = bot_info.get("data", {}).get("bot", bot_info.get("data", {}))
            name = bdata.get("app_name", bdata.get("name", ""))
            # open_id rides along from the same response — the trigger's
            # group @-mention gate matches against it.
            await mgr.update_bot_identity(
                body.agent_id,
                bot_name=name,
                bot_open_id=bdata.get("open_id", ""),
            )

    return result


@router.get("/auth/status")
async def lark_auth_status(request: Request, agent_id: str) -> dict[str, Any]:
    """Check the authentication status of the bound bot."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    result = await _cli._run_with_agent_id(["auth", "status"], agent_id=agent_id)

    # Sync auth status to DB
    if result.get("success"):
        data = result.get("data", {})
        new_status = determine_auth_status(data)
        if new_status != cred.auth_status:
            await mgr.update_auth_status(agent_id, new_status)
        data["db_auth_status"] = new_status

    return result


# ---- plugin contribution (batch 3c.5): this router belongs to builtin.channels.lark and is
# mounted by backend.plugins_host from backend.routes (no longer included by
# backend/main.py), so its paths 404 together with the plugin when disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("channel_lark", lambda: RouterSpec(router, "/api/lark", tags=('Lark',))),)
