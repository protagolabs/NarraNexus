"""
@file_name: _lark_service.py
@date: 2026-04-14
@description: Shared Lark business logic used by both HTTP routes and MCP tools.

Contains bind, owner resolution, and auth status helpers that must not
live in the API layer (backend/routes/) to avoid circular imports.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from ._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
    _encode_secret,
    AUTH_STATUS_BOT_READY,
    AUTH_STATUS_USER_LOGGED_IN,
    AUTH_STATUS_NOT_LOGGED_IN,
)
from .lark_cli_client import LarkCLIClient

# Shared CLI client (stateless, safe to share)
_cli = LarkCLIClient()

# Sentinel string from lark-cli auth status output
_LARK_NO_USERS_SENTINEL = "(no logged-in users)"


async def do_bind(
    mgr: LarkCredentialManager,
    agent_id: str,
    app_id: str,
    app_secret: str,
    brand: str,
    owner_email: str = "",
) -> dict[str, Any]:
    """Core bind logic shared between HTTP route and MCP tool.

    Returns {"success": True, "data": {...}} or {"success": False, "error": ...}.
    """
    profile_name = f"agent_{agent_id}"

    # Check if this agent already has a bot
    existing = await mgr.get_credential(agent_id)
    if existing:
        return {"success": False, "error": "Agent already has a Lark bot bound. Unbind first."}

    # Each Lark app can only be bound to one agent
    same_app = await mgr.get_by_app_id(app_id)
    if same_app:
        other_agents = [c.agent_id for c in same_app]
        return {
            "success": False,
            "error": (
                f"App ID {app_id} is already bound to agent(s): {', '.join(other_agents)}. "
                f"Each agent needs its own Lark app."
            ),
        }

    # Register CLI profile (--profile based isolation, works on all platforms)
    result = await _cli.config_init(profile_name, app_id, app_secret, brand)
    if not result.get("success"):
        return result

    # Save credential — bot identity works immediately, no OAuth needed
    cred = LarkCredential(
        agent_id=agent_id,
        app_id=app_id,
        app_secret_ref=f"appsecret:{app_id}",
        app_secret_encoded=_encode_secret(app_secret),
        brand=brand,
        profile_name=profile_name,
        auth_status=AUTH_STATUS_BOT_READY,
    )
    await mgr.save_credential(cred)

    # Try to get bot name (use V2 runner)
    bot_info = await _cli._run_v2(["contact", "+get-user", "--as", "bot"], agent_id)
    if bot_info.get("success"):
        data = bot_info.get("data", {})
        name = data.get("name", data.get("en_name", ""))
        if name:
            await mgr.update_bot_name(agent_id, name)

    # Resolve owner identity from email
    owner_open_id = ""
    owner_name = ""
    if owner_email:
        owner_open_id, owner_name = await resolve_owner(agent_id, owner_email)
        if owner_open_id:
            await mgr.update_owner(agent_id, owner_open_id, owner_name)

    return {
        "success": True,
        "data": {
            "profile_name": profile_name,
            "brand": brand,
            "app_id": app_id,
            "auth_status": AUTH_STATUS_BOT_READY,
            "owner_open_id": owner_open_id,
            "owner_name": owner_name,
        },
    }


async def resolve_owner(agent_id: str, owner_email: str) -> tuple[str, str]:
    """Resolve owner Lark identity from email. Returns (open_id, name).

    Uses V2 runner (HOME isolation) with fallback to --profile.
    """
    if not owner_email:
        return "", ""

    owner_open_id = ""
    owner_name = ""

    lookup = await _cli._run_v2(
        ["api", "POST", "/open-apis/contact/v3/users/batch_get_id",
         "--data", json.dumps({"emails": [owner_email]})],
        agent_id=agent_id,
    )
    if lookup.get("success"):
        user_list = lookup.get("data", {}).get("data", {}).get("user_list", [])
        if user_list:
            owner_open_id = user_list[0].get("user_id", "")

    if owner_open_id:
        user_info = await _cli._run_v2(
            ["contact", "+get-user", "--as", "bot", "--user-id", owner_open_id],
            agent_id=agent_id,
        )
        if user_info.get("success"):
            udata = user_info.get("data", {})
            user_obj = udata.get("user", udata)
            owner_name = user_obj.get("name", user_obj.get("en_name", ""))
        if not owner_name:
            owner_name = owner_email.split("@")[0].replace(".", " ").title()

    return owner_open_id, owner_name


def determine_auth_status(auth_data: dict) -> str:
    """Determine auth status from lark-cli auth status response data.

    Returns:
        - "user_logged_in" if user tokens exist (user OAuth completed)
        - "bot_ready" if only bot identity available
        - "not_logged_in" if neither
    """
    identity = auth_data.get("identity", "")
    users = auth_data.get("users", auth_data.get("userName", ""))
    token_status = auth_data.get("tokenStatus", "")

    # User tokens present → full OAuth done
    if identity == "user" or token_status == "valid":
        return AUTH_STATUS_USER_LOGGED_IN
    if users and users != _LARK_NO_USERS_SENTINEL:
        return AUTH_STATUS_USER_LOGGED_IN

    # Bot identity available → bot ready
    if identity == "bot":
        return AUTH_STATUS_BOT_READY

    return AUTH_STATUS_NOT_LOGGED_IN
