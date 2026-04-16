"""
@file_name: lark_cli_client.py
@date: 2026-04-10
@description: Unified wrapper for all lark-cli subprocess calls.

Two runners:
  - _run(args, profile)        — appends --profile, used for all regular commands
  - _run_v2(args, agent_id)    — resolves profile from agent_id, delegates to _run()
  - _run_with_home(args, ...)  — HOME isolation, only for `config init --new`

Kept business methods: config_init, profile_remove, get_user, send_message,
list_chat_messages. All other Lark operations go through the generic
lark_cli MCP tool (V2).
"""

from __future__ import annotations

import asyncio
import json

from loguru import logger


class LarkCLIClient:
    """Async wrapper around lark-cli subprocess calls."""

    # =========================================================================
    # Core runner
    # =========================================================================

    async def _run(
        self,
        args: list[str],
        profile: str,
        stdin_data: str = "",
        timeout: float = 30.0,
    ) -> dict:
        """
        Execute a lark-cli command and return parsed JSON.

        Appends --profile automatically. CLI defaults to JSON output for
        all commands, so --format is not needed (and Shortcuts don't support it).
        Returns {"success": True, "data": ...} or {"success": False, "error": ...}.
        """
        cmd = ["lark-cli"] + args + ["--profile", profile]

        logger.debug(f"lark-cli call: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode() if stdin_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Kill the subprocess to prevent zombie processes
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return {"success": False, "error": f"CLI command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": "lark-cli not found. Install: npm install -g @larksuite/cli"}

        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if proc.returncode != 0:
            # Try to parse structured error from stdout (CLI writes JSON errors to stdout)
            error_msg = stderr_str or stdout_str or f"CLI exited with code {proc.returncode}"
            try:
                parsed = json.loads(stdout_str)
                if isinstance(parsed, dict) and "error" in parsed:
                    error_msg = parsed["error"].get("message", error_msg)
            except (json.JSONDecodeError, AttributeError):
                pass
            return {"success": False, "error": error_msg}

        # Parse JSON output
        try:
            data = json.loads(stdout_str) if stdout_str else {}
        except json.JSONDecodeError:
            data = {"raw_output": stdout_str}

        return {"success": True, "data": data}

    # =========================================================================
    # V2 Core runner (--profile primary, HOME only for config init --new)
    # =========================================================================

    async def _run_v2(
        self,
        args: list[str],
        agent_id: str,
        stdin_data: str = "",
        timeout: float = 60.0,
    ) -> dict:
        """Execute a lark-cli command with per-agent --profile isolation.

        Primary strategy: --profile agent_{agent_id} (works on all platforms).
        Exception: `config init --new` uses HOME-based workspace isolation
        because --new is an interactive flow that doesn't support --profile.

        Returns {"success": True, "data": ...} or {"success": False, "error": ...}.
        """
        is_init_new = (
            len(args) >= 3
            and args[0] == "config"
            and args[1] == "init"
            and "--new" in args
        )

        if is_init_new:
            # HOME isolation only for interactive setup (no --profile support)
            return await self._run_with_home(args, agent_id, stdin_data, timeout)

        # All other commands: use --profile
        profile_name = f"agent_{agent_id}"
        return await self._run(args, profile_name, stdin_data=stdin_data, timeout=timeout)

    async def _run_with_home(
        self,
        args: list[str],
        agent_id: str,
        stdin_data: str = "",
        timeout: float = 60.0,
    ) -> dict:
        """Execute a lark-cli command with HOME-based workspace isolation.

        Only used for `config init --new` which doesn't support --profile.
        Sets HOME to the agent's workspace so ~/.lark-cli/ is per-agent.
        """
        from ._lark_workspace import get_home_env

        env = get_home_env(agent_id)
        cmd = ["lark-cli"] + args

        logger.debug(f"lark-cli HOME call [{agent_id}]: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode() if stdin_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return {"success": False, "error": f"CLI command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": "lark-cli not found. Install: npm install -g @larksuite/cli"}

        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if proc.returncode != 0:
            error_msg = stderr_str or stdout_str or f"CLI exited with code {proc.returncode}"
            error_data = {}
            try:
                parsed = json.loads(stdout_str)
                if isinstance(parsed, dict) and "error" in parsed:
                    err = parsed["error"]
                    error_msg = err.get("message", error_msg)
                    if "console_url" in err:
                        error_msg += f"\n\nEnable permission here: {err['console_url']}"
                    error_data = err
            except (json.JSONDecodeError, AttributeError):
                pass
            return {"success": False, "error": error_msg, "error_data": error_data}

        try:
            data = json.loads(stdout_str) if stdout_str else {}
        except json.JSONDecodeError:
            data = {"raw_output": stdout_str}

        return {"success": True, "data": data}

    # =========================================================================
    # Setup & Auth (used by _lark_service.py and routes)
    # =========================================================================

    async def config_init(
        self, profile: str, app_id: str, app_secret: str, brand: str
    ) -> dict:
        """Register a new CLI profile with App ID and Secret."""
        return await self._run(
            ["config", "init", "--app-id", app_id, "--app-secret-stdin",
             "--brand", brand, "--name", profile],
            profile=profile,
            stdin_data=app_secret,
        )

    async def profile_remove(self, profile: str) -> dict:
        """Remove a CLI profile."""
        return await self._run(["profile", "remove", profile], profile)

    # =========================================================================
    # Contact (used by lark_trigger.py for sender name resolution)
    # =========================================================================

    async def get_user(self, profile: str, user_id: str = "") -> dict:
        """Get user info. Omit user_id to get bot's own info."""
        args = ["contact", "+get-user", "--as", "bot"]
        if user_id:
            args.extend(["--user-id", user_id])
        return await self._run(args, profile)

    # =========================================================================
    # IM (used by lark_module.py ChannelSender and lark_context_builder.py)
    # =========================================================================

    async def send_message(
        self,
        profile: str,
        chat_id: str = "",
        user_id: str = "",
        text: str = "",
        markdown: str = "",
    ) -> dict:
        """Send a message to a chat or user."""
        args = ["im", "+messages-send"]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        elif user_id:
            args.extend(["--user-id", user_id])
        if text:
            args.extend(["--text", text])
        elif markdown:
            args.extend(["--markdown", markdown])
        return await self._run(args, profile)

    async def list_chat_messages(
        self,
        profile: str,
        chat_id: str = "",
        user_id: str = "",
        limit: int = 20,
    ) -> dict:
        """List recent messages in a chat or P2P conversation."""
        args = ["im", "+chat-messages-list", "--as", "bot"]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        elif user_id:
            args.extend(["--user-id", user_id])
        args.extend(["--page-size", str(limit)])
        return await self._run(args, profile)
