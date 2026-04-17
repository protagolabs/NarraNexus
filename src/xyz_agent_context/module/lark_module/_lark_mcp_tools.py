"""
@file_name: _lark_mcp_tools.py
@date: 2026-04-16
@description: Lark MCP tools — single generic lark_cli tool + lifecycle tools.

Tools exposed:
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


def register_lark_mcp_tools(mcp: Any) -> None:
    """Register Lark MCP tools and resources on the given FastMCP server."""

    # =====================================================================
    # Core Tool: lark_cli
    # =====================================================================

    @mcp.tool()
    async def lark_cli(agent_id: str, command: str) -> dict:
        """
        Run any lark-cli command with per-agent profile isolation. This is
        the main execution tool for ALL Lark data operations.

        **WHEN TO CALL**: any time you need to interact with Lark data —
        send messages, search contacts, read/create docs, query calendar,
        manage tasks, etc.

        **BEFORE FIRST USE OF A DOMAIN** (in this session): call
        `lark_skill(agent_id, name)` to load that domain's SKILL.md. The
        skill doc teaches correct syntax and identity rules — without it
        you'll waste turns guessing. Example: before any `im +...` command,
        call `lark_skill(agent_id, "lark-im")`.

        **COMMAND FORMAT**: whatever you'd type after `lark-cli`. Examples:
          - "im +messages-send --user-id ou_xxx --text hello --as bot"
          - "contact +search-user --query 'John Smith' --as user"
          - "calendar +agenda --as user"
          - "docs +create --title 'My Doc' --markdown '# Content' --as bot"
          - "schema im.messages.create"    (look up API field definitions)
          - "im +messages-send --help"     (discover a command's flags)

        **IDENTITY — pick the right `--as`** (required on most commands):
          - `--as bot`: sending messages, creating docs, actions the app performs
          - `--as user`: search/read that requires user identity (contact
            search, message search, doc search)

        **DO NOT**:
          - add `--profile` — profile isolation is injected automatically
          - add `--format json` — Shortcut commands (the ones with `+`) reject it
          - shell out to `lark-cli` via Bash — always use this tool

        **ON FAILURE**:
          - error contains "missing scope X" or "permission denied"
            → call `lark_auth(agent_id, scopes="X")`, send URL to user
          - "Command blocked" (whitelist hit)
            → you tried a lifecycle command; use the dedicated tool instead
              (lark_setup / lark_auth / lark_auth_complete)
          - "No Lark bot bound"
            → call `lark_setup(agent_id, ...)` first

        Args:
            agent_id: The agent performing this action.
            command: The lark-cli command string (WITHOUT the "lark-cli"
                     prefix and WITHOUT --profile).

        Returns:
            {"success": True, "data": <parsed CLI output>} or
            {"success": False, "error": "..."}.
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
        return await _cli._run_with_agent_id(args, agent_id)

    # =====================================================================
    # Lifecycle: lark_setup
    # =====================================================================

    @mcp.tool()
    async def lark_setup(agent_id: str, brand: str = "lark", owner_email: str = "") -> dict:
        """
        Create a new Lark/Feishu app and bind it as this agent's bot. Replaces
        the manual 9-step app-creation process with a single authorization URL.

        **WHEN TO CALL**: the user asks to "connect Lark / Feishu", "set up
        Lark", or similar. Only works when the agent has NO bot bound yet.

        **BEFORE CALLING — COLLECT FROM USER (always ask, do NOT silently
        default)**:
          1. `brand`: are they using "feishu" (飞书 · 中国大陆) or "lark"
             (Lark · International)? These are DIFFERENT platforms. Example
             opening line: "To set up Lark I need two things — are you on
             Feishu (飞书) or Lark International? And what's your Lark /
             Feishu email for identity linking?"
          2. `owner_email`: their Lark/Feishu email. Used after bind to find
             their `open_id` in the org directory so you know who "me" is.

        **AFTER RETURN**:
          - `success=True` with `data.auth_url` → send the URL to the user
            verbatim (it IS the authorization link). Tell them:
              "Please open this link in your browser to finish app creation.
               Tell me when you're done."
            The app is created once the user authorizes in the browser; the
            bot is then automatically bound to this agent.
          - After the user confirms they're done → call `lark_status` to
            verify the binding is healthy.

        **FAILURE**:
          - "Agent already has a Lark bot" → tell the user; offer to unbind
            (they can do so from the frontend LarkConfig panel, or via
            DELETE /api/lark/unbind) before trying again.
          - URL extraction timeout / "Could not extract setup URL"
            → usually a network or lark-cli installation issue. Tell the
            user exactly what happened; don't silently retry.

        Args:
            agent_id: The agent to set up.
            brand: "feishu" (中国大陆) or "lark" (International). Default
                   "lark" is a fallback — ALWAYS confirm with the user first.
            owner_email: User's Lark/Feishu email address.

        Returns:
            {"success": True, "data": {"auth_url": "...", ...}} or error.
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
        Initiate an OAuth login flow to grant the bound bot one or more
        permission scopes. Returns a verification URL + device_code.

        **WHEN TO CALL** (one of):
          - A previous `lark_cli` call failed with "missing scope X",
            "permission denied", or a similar scope error → pass the missing
            scope name in `scopes`.
          - The user explicitly asks to grant more permissions / complete
            OAuth / re-authorize.

        **DO NOT CALL PREEMPTIVELY**: never request scopes "just in case" or
        before the user's first real action. Unnecessary auth prompts annoy
        users. Wait for an actual failure or explicit user request.

        **AFTER RETURN**:
          1. `data.verification_url` and `data.device_code` are returned.
          2. Send the URL to the user — do not annotate it, just present
             it as "the authorization link".
          3. The user will see one of two buttons:
             - **Authorize** → a single click completes the grant.
             - **Submit for approval** → the user is requesting permissions
               that need admin approval. They click, wait for an admin, then
               come back. You may need to call lark_auth again to get a
               fresh URL after approval.
          4. After the user confirms they clicked "Authorize" → call
             `lark_auth_complete(agent_id, device_code)` with the SAME
             device_code you got here.

        **FAILURE**:
          - "No Lark bot bound" → call `lark_setup` first.
          - Timeout → retry once. If it times out again, ask the user about
            network issues; don't silently loop.

        Args:
            agent_id: The agent whose bot to authorize.
            scopes: Space-separated scope names as surfaced by Lark error
                    messages. Example: "im:chat:create contact:user.base:readonly".
                    If empty, falls back to `--recommend` (Lark's default
                    bundle). Prefer specific scopes when you know what's
                    missing.

        Returns:
            {"success": True, "data": {"verification_url": "...",
             "device_code": "...", "next_step": "..."}} or error.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound. Use lark_setup first."}

        if scopes:
            args = ["auth", "login", "--scope", scopes, "--json", "--no-wait"]
        else:
            args = ["auth", "login", "--recommend", "--json", "--no-wait"]

        result = await _cli._run_with_agent_id(
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
        Finalize an OAuth login flow after the user has clicked the
        authorization URL. Exchanges the device_code for tokens and flips
        `auth_status` to `user_logged_in`.

        **WHEN TO CALL**: the user confirms they clicked "Authorize" on the
        URL you got from `lark_auth`. Typical cues: "done", "authorized",
        "I clicked it", "完成了", etc. Call immediately — don't wait or poll.

        **DO NOT POLL OR PREEMPT**: only call this once, in direct response
        to user confirmation. Do not loop or retry unless explicitly told.

        **BEFORE CALLING — COLLECT FROM USER**:
          - Just verbal confirmation that they clicked "Authorize" (not
            "Submit for approval"). You already have `device_code` from
            your earlier `lark_auth` call; NEVER ask the user for it.

        **AFTER RETURN**:
          - `success=True` → retry the original command that triggered the
            auth flow. Tell the user briefly: "Authorized, retrying..."
          - `success=False` → most likely the user clicked "Submit for
            approval" (admin pending) rather than "Authorize" directly.
            Tell them: "Looks like the grant isn't active yet — once your
            admin approves or you finish the Authorize click, let me know
            and I'll retry."

        Args:
            agent_id: The agent whose bot is being authorized.
            device_code: The device_code returned by the preceding
                         `lark_auth` call in THIS session.

        Returns:
            {"success": True, "data": {...}} or error.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        result = await _cli._run_with_agent_id(
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
        Check the bound Lark bot's auth state and connectivity. Combines
        `auth status` (identity + login state) with `doctor` (network and
        CLI sanity checks).

        **WHEN TO CALL**:
          - Just after `lark_setup` completes, to verify the bind.
          - When a `lark_cli` call fails with a vague error and you want
            to know whether the bot itself is healthy.
          - When the user asks "is Lark working?", "what bot am I using?",
            "am I logged in?", etc.

        **DO NOT CALL PREEMPTIVELY**: never before every `lark_cli` — that
        wastes a round-trip. Trust the normal error paths in typical use.

        **AFTER RETURN**:
          - `auth.status` is one of `not_logged_in` / `bot_ready` /
            `user_logged_in` / `expired`. If `expired` → call `lark_auth`
            to re-authorize.
          - `doctor` fields show network / CLI / config issues. Surface
            any problems to the user in plain language — don't silently
            retry on network errors.

        Args:
            agent_id: The agent to check.

        Returns:
            {"success": True, "data": {"auth": {...}, "doctor": {...}}}.
        """
        cred = await _get_credential(agent_id)
        if not cred:
            return {"success": False, "error": "No Lark bot bound."}

        auth = await _cli._run_with_agent_id(["auth", "status"], agent_id)
        doctor = await _cli._run_with_agent_id(["doctor"], agent_id)

        return {
            "success": True,
            "data": {
                "auth": auth.get("data", {}),
                "doctor": doctor.get("data", {}),
            },
        }

    # =====================================================================
    # Skill doc loader
    # =====================================================================

    @mcp.tool()
    async def lark_skill(agent_id: str, name: str) -> dict:
        """
        Load the SKILL.md knowledge doc for a Lark CLI domain.

        **WHEN TO CALL**: Before using a Lark domain you haven't used in this
        session. The SKILL.md teaches you correct command syntax, identity
        rules (--as user vs --as bot), ID types (open_id / chat_id / user_id),
        and common gotchas. Reading it first prevents multi-turn trial-and-error.

        **RECOMMENDED FIRST CALL**: lark_skill(agent_id, "lark-shared") —
        covers authentication, permission handling, and --as user/bot rules
        that apply to all other domains.

        **AVAILABLE SKILLS** (call this tool to load any):
        - lark-shared: authentication, permissions, --as user/bot (read first)
        - lark-im: messaging — send/reply/search messages, manage chats
        - lark-contact: people search by email / name / phone
        - lark-calendar: agenda, create events, free/busy query
        - lark-doc: create and edit Lark docs
        - lark-sheets: spreadsheets read/write
        - lark-drive: file upload/download, folder management
        - lark-mail: email draft/compose/send/reply/search
        - lark-task: todo and checklist management
        - lark-wiki: knowledge space navigation
        - lark-vc: video meeting recordings and summaries
        - lark-minutes: meeting minutes AI summaries
        - lark-base: multi-dimensional tables (Base)
        - lark-event: realtime event subscription
        - lark-whiteboard: charts / flowcharts / mindmaps
        - lark-workflow-meeting-summary / lark-workflow-standup-report
        - lark-openapi-explorer / lark-skill-maker (advanced)

        **FAILURE**: unknown skill name → returns the available list so you
        can pick a valid one.

        Args:
            agent_id: The agent performing this action. Kept for API
                      consistency with other Lark tools; skill content is
                      the same across all agents.
            name: Skill name without the "lark-" requirement-free form.
                  Accepts either "lark-im" or "im". See list above.

        Returns:
            {"success": True, "name": "lark-im", "content": "<markdown>"} or
            {"success": False, "error": "...", "available": ["lark-im", ...]}.
        """
        from ._lark_skill_loader import get_available_skills, load_skill_content
        # Accept both "im" and "lark-im" forms
        normalized = name if name.startswith("lark-") else f"lark-{name}"
        content = load_skill_content(normalized)
        if not content:
            return {
                "success": False,
                "error": f"Skill '{normalized}' not found.",
                "available": get_available_skills(),
            }
        return {"success": True, "name": normalized, "content": content}
