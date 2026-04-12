"""
@file_name: lark.py
@date: 2026-04-10
@description: Backend API routes for Lark/Feishu bot binding, auth, and management.

Endpoints:
  POST   /api/lark/bind          — Bind a Lark bot to an agent
  POST   /api/lark/auth/login    — Initiate OAuth login (returns auth URL)
  POST   /api/lark/auth/complete — Complete OAuth with device code
  GET    /api/lark/auth/status   — Check login status
  POST   /api/lark/test          — Test connection
  DELETE /api/lark/unbind        — Unbind a Lark bot
  GET    /api/lark/credential    — Get credential info for an agent
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from loguru import logger

from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient
from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
)

router = APIRouter()
_cli = LarkCLIClient()


# =========================================================================
# Request / Response schemas
# =========================================================================

class BindRequest(BaseModel):
    agent_id: str
    app_id: str
    app_secret: str
    brand: str = "feishu"  # "feishu" or "lark"
    owner_email: str = ""  # Owner's email to auto-resolve Lark open_id


class AgentRequest(BaseModel):
    agent_id: str


class AuthCompleteRequest(BaseModel):
    agent_id: str
    device_code: str


# =========================================================================
# Helper
# =========================================================================

async def _get_db():
    """Get database client via factory (same pattern as other routes)."""
    from xyz_agent_context.utils.db_factory import get_db_client
    return await get_db_client()


# =========================================================================
# Endpoints
# =========================================================================

@router.post("/bind")
async def bind_lark_bot(body: BindRequest):
    """Bind a Lark/Feishu bot to an agent."""
    if body.brand not in ("feishu", "lark"):
        return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

    db = await _get_db()
    mgr = LarkCredentialManager(db)
    profile_name = f"agent_{body.agent_id}"

    # Check if this agent already has a bot
    existing = await mgr.get_credential(body.agent_id)
    if existing:
        return {"success": False, "error": "Agent already has a Lark bot bound. Unbind first."}

    # Each Lark app can only be bound to one agent — shared bots cause
    # ambiguity (same bot name for all agents) and event routing conflicts.
    same_app = await mgr.get_by_app_id(body.app_id)
    if same_app:
        other_agents = [c.agent_id for c in same_app]
        return {
            "success": False,
            "error": (
                f"App ID {body.app_id} is already bound to agent(s): {', '.join(other_agents)}. "
                f"Each agent needs its own Lark app. Create a new app on the Lark Open Platform, "
                f"or unbind the other agent first."
            ),
        }

    # Register CLI profile
    result = await _cli.config_init(profile_name, body.app_id, body.app_secret, body.brand)
    if not result.get("success"):
        return result

    # Save credential — bot identity works immediately, no OAuth needed
    cred = LarkCredential(
        agent_id=body.agent_id,
        app_id=body.app_id,
        app_secret_ref=f"appsecret:{body.app_id}",
        brand=body.brand,
        profile_name=profile_name,
        auth_status="logged_in",
    )
    await mgr.save_credential(cred)

    # Try to get bot name
    bot_info = await _cli.get_user(profile_name)
    if bot_info.get("success"):
        data = bot_info.get("data", {})
        name = data.get("name", data.get("en_name", ""))
        if name:
            await mgr.update_bot_name(body.agent_id, name)

    # Resolve owner Lark identity from email
    owner_open_id = ""
    owner_name = ""
    if body.owner_email:
        lookup = await _cli._run(
            ["api", "POST", "/open-apis/contact/v3/users/batch_get_id",
             "--data", json.dumps({"emails": [body.owner_email]})],
            profile=profile_name,
        )
        if lookup.get("success"):
            user_list = lookup.get("data", {}).get("data", {}).get("user_list", [])
            if user_list:
                owner_open_id = user_list[0].get("user_id", "")
        if owner_open_id:
            user_info = await _cli.get_user(profile_name, user_id=owner_open_id)
            if user_info.get("success"):
                udata = user_info.get("data", {})
                user_obj = udata.get("user", udata)
                owner_name = user_obj.get("name", user_obj.get("en_name", ""))
            if not owner_name and body.owner_email:
                owner_name = body.owner_email.split("@")[0].replace(".", " ").title()

    if owner_open_id:
        await mgr.update_owner(body.agent_id, owner_open_id, owner_name)
        logger.info(f"Lark owner resolved: {owner_name} ({owner_open_id})")

    logger.info(f"Lark bot bound: agent={body.agent_id}, app_id={body.app_id}")
    return {
        "success": True,
        "data": {
            "profile_name": profile_name,
            "brand": body.brand,
            "app_id": body.app_id,
            "auth_status": "logged_in",
            "owner_open_id": owner_open_id,
            "owner_name": owner_name,
        },
    }


@router.post("/auth/login")
async def lark_auth_login(body: AgentRequest):
    """Initiate OAuth login. Returns auth URL for browser authorization."""
    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    result = await _cli.auth_login(cred.profile_name, no_wait=True)
    return result


@router.post("/auth/complete")
async def lark_auth_complete(body: AuthCompleteRequest):
    """Complete OAuth login with device code from a previous --no-wait call."""
    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    result = await _cli.auth_login_complete(cred.profile_name, body.device_code)

    # Update auth status on success
    if result.get("success"):
        await mgr.update_auth_status(body.agent_id, "logged_in")

        # Try to get bot name
        bot_info = await _cli.get_user(cred.profile_name)
        if bot_info.get("success"):
            data = bot_info.get("data", {})
            name = data.get("name", data.get("en_name", ""))
            if name:
                await mgr.update_bot_name(body.agent_id, name)

    return result


@router.get("/auth/status")
async def lark_auth_status(agent_id: str):
    """Check the authentication status of the bound bot."""
    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    result = await _cli.auth_status(cred.profile_name)

    # Sync auth status to DB
    if result.get("success"):
        data = result.get("data", {})
        users = data.get("users", "(no logged-in users)")
        new_status = "logged_in" if users != "(no logged-in users)" else "not_logged_in"
        if new_status != cred.auth_status:
            await mgr.update_auth_status(agent_id, new_status)
        result["data"]["db_auth_status"] = new_status

    return result


@router.post("/test")
async def test_lark_connection(body: AgentRequest):
    """Test connection by getting bot's own info."""
    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    return await _cli.get_user(cred.profile_name)


@router.delete("/unbind")
async def unbind_lark_bot(body: AgentRequest):
    """Unbind Lark bot from agent. Removes CLI profile and DB record."""
    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(body.agent_id)

    if not cred:
        return {"success": False, "error": "No Lark bot bound to this agent."}

    # Remove CLI profile
    await _cli.profile_remove(cred.profile_name)

    # Remove DB record
    await mgr.delete_credential(body.agent_id)

    logger.info(f"Lark bot unbound: agent={body.agent_id}")
    return {"success": True}


@router.get("/credential")
async def get_lark_credential(agent_id: str):
    """Get Lark credential info for an agent (no secrets exposed)."""
    db = await _get_db()
    mgr = LarkCredentialManager(db)
    cred = await mgr.get_credential(agent_id)

    if not cred:
        return {"success": True, "data": None}

    return {
        "success": True,
        "data": {
            "agent_id": cred.agent_id,
            "app_id": cred.app_id,
            "brand": cred.brand,
            "bot_name": cred.bot_name,
            "owner_open_id": cred.owner_open_id,
            "owner_name": cred.owner_name,
            "auth_status": cred.auth_status,
            "is_active": cred.is_active,
        },
    }
