"""
@file_name: _lark_mcp_tools_v2.py
@date: 2026-04-16
@description: V2 MCP tools — single generic lark_cli tool + lifecycle tools.

Replaces 19 individual MCP tools with:
  - lark_cli(agent_id, command)      — Run any lark-cli command (whitelist enforced)
  - lark_setup(agent_id)             — Create new Lark app via config init --new
  - lark_auth(agent_id)              — Initiate OAuth login
  - lark_auth_complete(agent_id, dc) — Complete OAuth device flow
  - lark_status(agent_id)            — Check auth + connectivity

Plus MCP Resources for Skill docs (on-demand Agent knowledge).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from ._lark_credential_manager import (
    LarkCredentialManager,
    AUTH_STATUS_BOT_READY,
    AUTH_STATUS_USER_LOGGED_IN,
)
from .lark_cli_client import LarkCLIClient
from ._lark_command_security import validate_command, sanitize_command
from ._lark_workspace import ensure_workspace, get_home_env

# Shared CLI client instance (stateless)
_cli = LarkCLIClient()


async def _get_credential(agent_id: str):
    """Load credential from DB via MCP-level database client."""
    db = await XYZBaseModule.get_mcp_db_client()
    mgr = LarkCredentialManager(db)
    return await mgr.get_credential(agent_id)


def register_lark_mcp_tools_v2(mcp: Any) -> None:
    """Register V2 Lark MCP tools and resources on the given FastMCP server."""

    # =====================================================================
    # Core Tool: lark_cli
    # =====================================================================

    @mcp.tool()
    async def lark_cli(agent_id: str, command: str) -> dict:
        """
        Run any lark-cli command with per-agent isolation.

        The command string is what you would type after `lark-cli`.
        Examples:
          - "im +messages-send --user-id ou_xxx --text hello"
          - "contact +search-user --query John"
          - "calendar +agenda"
          - "docs +create --as bot --title 'My Doc' --markdown '# Content'"
          - "schema im.messages.create"
          - "im +messages-send --help"

        Security: Commands are validated against a whitelist. Dangerous operations
        (config changes, auth login, profile removal) are blocked — use dedicated tools.

        Note: Do NOT add --format json to Shortcut commands (commands with +).
        Do NOT add --profile — isolation is automatic.

        Args:
            agent_id: The agent performing this action.
            command: The lark-cli command (without "lark-cli" prefix).
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Use lark_setup to create one."}

        # Validate command
        allowed, reason = validate_command(command)
        if not allowed:
            return {"success": False, "error": f"Command blocked: {reason}"}

        # Parse into args
        try:
            args = sanitize_command(command)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # Execute with HOME isolation
        return await _cli._run_v2(args, agent_id)

    # =====================================================================
    # Lifecycle: lark_setup
    # =====================================================================

    @mcp.tool()
    async def lark_setup(agent_id: str, brand: str = "lark", owner_email: str = "") -> dict:
        """
        Create a new Lark/Feishu app for this agent. This replaces the manual
        9-step setup process.

        Returns an authorization URL — the user must open it in a browser to
        complete app creation. After that, the bot is automatically configured.

        IMPORTANT: Always ask the user for their email (owner_email) so the
        Agent knows who they are.

        Args:
            agent_id: The agent to set up.
            brand: "feishu" (China) or "lark" (International, default).
            owner_email: User's Lark/Feishu email to link their identity.
        """
        if brand not in ("feishu", "lark"):
            return {"success": False, "error": "brand must be 'feishu' or 'lark'."}

        # Check if already bound
        cred = await _get_credential(agent_id)
        if cred:
            return {"success": False, "error": "Agent already has a Lark bot. Unbind first."}

        # Create workspace
        workspace = ensure_workspace(agent_id)
        env = get_home_env(agent_id)

        # Run config init --new
        # This command prints a QR code + authorization URL, then blocks
        # waiting for the user to complete in browser. We need to:
        # 1. Capture all output until we find the URL
        # 2. Return the URL immediately (don't wait for user to finish)
        # 3. Leave the process running in background
        import asyncio
        import re
        try:
            # Merge stderr into stdout — some CLI versions may print to either
            proc = await asyncio.create_subprocess_exec(
                "lark-cli", "config", "init", "--new", "--brand", brand,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )

            # Read output with a timeout — URL appears within first few seconds
            collected = b""
            try:
                async def _read_until_url():
                    nonlocal collected
                    while True:
                        chunk = await proc.stdout.read(4096)
                        if not chunk:
                            break
                        collected += chunk
                        if b"http" in collected.lower():
                            # Read a bit more to get the full URL line
                            try:
                                extra = await asyncio.wait_for(
                                    proc.stdout.read(4096), timeout=2.0
                                )
                                if extra:
                                    collected += extra
                            except asyncio.TimeoutError:
                                pass
                            return

                await asyncio.wait_for(_read_until_url(), timeout=30.0)
            except asyncio.TimeoutError:
                # Kill if no URL appeared within 30s
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                return {
                    "success": False,
                    "error": "Timed out waiting for setup URL from CLI.",
                    "raw_output": collected.decode(errors="replace")[:2000],
                }

            # Extract URL from collected output
            output_text = collected.decode(errors="replace")
            urls = re.findall(r'https?://\S+', output_text)
            if not urls:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                return {
                    "success": False,
                    "error": "Could not extract setup URL from CLI output.",
                    "raw_output": output_text[:2000],
                }
            auth_url = urls[0]

            # Process continues running in background — it will complete
            # when the user finishes browser authorization.
            # We don't wait for it; credential_watcher will detect completion.

            # Save initial credential
            db = await XYZBaseModule.get_mcp_db_client()
            mgr = LarkCredentialManager(db)
            from ._lark_credential_manager import LarkCredential
            cred = LarkCredential(
                agent_id=agent_id,
                app_id="pending_setup",
                app_secret_ref="",
                brand=brand,
                profile_name=f"agent_{agent_id}",
                workspace_path=str(workspace),
                auth_status="not_logged_in",
            )
            await mgr.save_credential(cred)

            return {
                "success": True,
                "data": {
                    "auth_url": auth_url,
                    "workspace": str(workspace),
                    "message": (
                        "Open the URL in a browser to create your Lark app. "
                        "After completing setup, come back and tell me."
                    ),
                },
            }

        except FileNotFoundError:
            return {"success": False, "error": "lark-cli not found. Install: npm install -g @larksuite/cli"}
        except Exception as e:
            return {"success": False, "error": f"Setup failed: {e}"}

    # =====================================================================
    # Lifecycle: lark_auth + lark_auth_complete
    # =====================================================================

    @mcp.tool()
    async def lark_auth(agent_id: str, scopes: str = "") -> dict:
        """
        Initiate OAuth login for the bound Lark bot. Returns a verification
        URL and device_code.

        ONLY call this when:
        - A command fails with 'missing scope' or 'permission denied'
        - The user explicitly asks to complete Lark OAuth

        If a specific scope is needed (from an error message), pass it in the
        scopes parameter. Otherwise uses --recommend for all common permissions.

        After the user clicks the URL and authorizes, call lark_auth_complete
        with the device_code.

        The user may see:
        - "Authorize" → just click. Done!
        - "Submit for approval" → click to request permissions, wait for admin
          approval, then come back and ask for a new link.

        Args:
            agent_id: The agent whose bot to authorize.
            scopes: Space-separated scope names (e.g. "im:chat im:chat:create").
                    If empty, uses --recommend for all recommended permissions.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Use lark_setup first."}

        if scopes:
            args = ["auth", "login", "--scope", scopes, "--json", "--no-wait"]
        else:
            args = ["auth", "login", "--recommend", "--json", "--no-wait"]

        result = await _cli._run_v2(
            args,
            agent_id,
            timeout=60.0,
        )
        if result.get("success"):
            data = result.get("data", {})
            device_code = data.get("device_code", "")
            if device_code:
                result["data"]["next_step"] = (
                    "Send the verification_url to the user. "
                    "After they authorize, call lark_auth_complete with this device_code."
                )
        return result

    @mcp.tool()
    async def lark_auth_complete(agent_id: str, device_code: str) -> dict:
        """
        Complete OAuth login after user has authorized in browser.
        Call this AFTER the user confirms they clicked the authorization link.

        Args:
            agent_id: The agent whose bot is being authorized.
            device_code: The device_code returned by lark_auth.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        result = await _cli._run_v2(
            ["auth", "login", "--device-code", device_code, "--json"],
            agent_id,
            timeout=60.0,
        )
        if result.get("success"):
            db = await XYZBaseModule.get_mcp_db_client()
            mgr = LarkCredentialManager(db)
            await mgr.update_auth_status(agent_id, AUTH_STATUS_USER_LOGGED_IN)
        return result

    # =====================================================================
    # Lifecycle: lark_status
    # =====================================================================

    @mcp.tool()
    async def lark_status(agent_id: str) -> dict:
        """
        Check auth and connection health for the bound Lark bot.
        Returns identity type, login status, and connectivity diagnostics.

        Args:
            agent_id: The agent to check.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        auth = await _cli._run_v2(["auth", "status"], agent_id)
        doctor = await _cli._run_v2(["doctor"], agent_id)

        return {
            "success": True,
            "data": {
                "auth": auth.get("data", {}),
                "doctor": doctor.get("data", {}),
            },
        }

    # =====================================================================
    # MCP Resources: Skill docs
    # =====================================================================

    try:
        from ._lark_skill_loader import get_available_skills, load_skill_content
        from fastmcp.resources import FunctionResource

        available = get_available_skills()
        if available:
            for skill_name in available:
                # Use add_resource with FunctionResource — avoids FastMCP
                # decorator issue where URI without template params rejects
                # functions with parameters.
                def _make_loader(name: str):
                    async def loader() -> str:
                        content = load_skill_content(name)
                        return content or f"Skill '{name}' not found."
                    return loader

                resource = FunctionResource(
                    uri=f"lark://skills/{skill_name}",
                    name=skill_name,
                    description=f"Lark CLI skill docs for {skill_name}",
                    fn=_make_loader(skill_name),
                )
                mcp.add_resource(resource)

            logger.info(f"Registered {len(available)} Lark skill resources: {', '.join(available)}")
        else:
            logger.warning("No Lark CLI skills found. Install: npx skills add larksuite/cli -y -g")
    except Exception as e:
        logger.warning(f"Failed to register skill resources: {e}")
