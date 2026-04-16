"""
@file_name: lark_cli_client.py
@date: 2026-04-10
@description: Unified wrapper for all lark-cli subprocess calls.

Every CLI invocation goes through _run(), which auto-appends --profile and
--format json. Business methods map 1:1 to CLI shortcuts.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from loguru import logger

# Allowed Lark document hostnames for SSRF protection
_ALLOWED_LARK_HOSTS = {
    "open.feishu.cn",
    "open.larksuite.com",
    "feishu.cn",
    "larksuite.com",
    "bytedance.feishu.cn",
}


def _validate_lark_url(url: str) -> bool:
    """Validate that a URL points to a legitimate Lark domain."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http"):
            return False
        hostname = parsed.hostname or ""
        return any(hostname == h or hostname.endswith(f".{h}") for h in _ALLOWED_LARK_HOSTS)
    except Exception:
        return False


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
    # Setup & Auth
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

    async def auth_login(self, profile: str, no_wait: bool = True) -> dict:
        """Initiate OAuth login with --recommend (requests all recommended scopes).

        If some permissions aren't enabled yet, the user will see a
        'submit for approval' page — this is expected and lets them
        request all needed permissions in one step.
        After approval, clicking the link again will just authorize.
        """
        args = ["auth", "login", "--recommend", "--json"]
        if no_wait:
            args.append("--no-wait")
        return await self._run(args, profile, timeout=60.0)

    async def auth_login_complete(self, profile: str, device_code: str) -> dict:
        """Complete OAuth login with device code from a previous --no-wait call."""
        return await self._run(
            ["auth", "login", "--device-code", device_code, "--json"],
            profile,
            timeout=60.0,

        )

    async def auth_status(self, profile: str) -> dict:
        """Check current authentication status."""
        return await self._run(["auth", "status"], profile)

    async def profile_remove(self, profile: str) -> dict:
        """Remove a CLI profile."""
        return await self._run(["profile", "remove", profile], profile)

    # =========================================================================
    # Contact
    # =========================================================================

    async def search_user(self, profile: str, query: str) -> dict:
        """Search users by name, email, or phone.

        Uses the API layer (batch_get_id) for email/phone lookups (works with bot identity).
        Falls back to the Shortcut command for name searches (requires user identity).
        """
        # If query looks like an email, use batch_get_id (bot-compatible)
        if "@" in query:
            result = await self._run(
                ["api", "POST", "/open-apis/contact/v3/users/batch_get_id",
                 "--data", json.dumps({"emails": [query]})],
                profile,
            )
            return result
        # If query looks like a phone number, use batch_get_id
        if query.startswith("+") or query.replace("-", "").isdigit():
            result = await self._run(
                ["api", "POST", "/open-apis/contact/v3/users/batch_get_id",
                 "--data", json.dumps({"mobiles": [query]})],
                profile,
            )
            return result
        # Name search — requires user identity (Shortcut command)
        return await self._run(
            ["contact", "+search-user", "--query", query],
            profile,
        )

    async def get_user(self, profile: str, user_id: str = "") -> dict:
        """Get user info. Omit user_id to get bot's own info."""
        args = ["contact", "+get-user", "--as", "bot"]
        if user_id:
            args.extend(["--user-id", user_id])
        return await self._run(args, profile)

    # =========================================================================
    # IM (Messaging)
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

    async def reply_message(self, profile: str, message_id: str, text: str) -> dict:
        """Reply to a specific message."""
        return await self._run(
            ["im", "+messages-reply", "--message-id", message_id, "--text", text],
            profile,
        )

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

    async def search_messages(
        self, profile: str, query: str, chat_id: str = ""
    ) -> dict:
        """Search messages by keyword. Optionally filter by chat_id."""
        args = ["im", "+messages-search", "--query", query]
        if chat_id:
            args.extend(["--chat-id", chat_id])
        return await self._run(args, profile)

    async def create_chat(
        self, profile: str, name: str, user_ids: Optional[list[str]] = None
    ) -> dict:
        """Create a group chat."""
        args = ["im", "+chat-create", "--as", "bot", "--name", name]
        if user_ids:
            for uid in user_ids:
                args.extend(["--user-ids", uid])
        return await self._run(args, profile)

    async def search_chat(self, profile: str, query: str) -> dict:
        """Search group chats by keyword."""
        return await self._run(
            ["im", "+chat-search", "--as", "bot", "--query", query],
            profile,
        )

    # =========================================================================
    # Docs
    # =========================================================================

    async def create_document(self, profile: str, title: str, markdown: str) -> dict:
        """Create a new Lark document with Markdown content."""
        return await self._run(
            ["docs", "+create", "--as", "bot", "--title", title, "--markdown", markdown],
            profile,
        )

    async def fetch_document(self, profile: str, doc_url: str) -> dict:
        """Read a Lark document's content by URL."""
        if not _validate_lark_url(doc_url):
            return {"success": False, "error": "Invalid document URL. Must be a Lark/Feishu domain."}
        return await self._run(
            ["docs", "+fetch", "--as", "bot", "--url", doc_url],
            profile,
        )

    async def update_document(self, profile: str, doc_url: str, markdown: str) -> dict:
        """Update an existing Lark document."""
        if not _validate_lark_url(doc_url):
            return {"success": False, "error": "Invalid document URL. Must be a Lark/Feishu domain."}
        return await self._run(
            ["docs", "+update", "--as", "bot", "--url", doc_url, "--markdown", markdown],
            profile,
        )

    async def search_documents(self, profile: str, query: str) -> dict:
        """Search documents, Wiki pages, and spreadsheets."""
        return await self._run(
            ["docs", "+search", "--as", "bot", "--query", query],
            profile,
        )

    # =========================================================================
    # Calendar
    # =========================================================================

    async def get_agenda(self, profile: str, date: str = "") -> dict:
        """View calendar agenda. Defaults to today."""
        args = ["calendar", "+agenda"]
        if date:
            args.extend(["--start", date])
        return await self._run(args, profile)

    async def create_event(
        self,
        profile: str,
        summary: str,
        start: str,
        end: str,
        attendees: Optional[list[str]] = None,
    ) -> dict:
        """Create a calendar event."""
        args = ["calendar", "+create", "--summary", summary,
                "--start", start, "--end", end]
        if attendees:
            for a in attendees:
                args.extend(["--attendee", a])
        return await self._run(args, profile)

    async def freebusy(
        self, profile: str, user_ids: list[str], start: str, end: str
    ) -> dict:
        """Query free/busy status for users."""
        args = ["calendar", "+freebusy", "--start", start, "--end", end]
        for uid in user_ids:
            args.extend(["--user-id", uid])
        return await self._run(args, profile)

    # =========================================================================
    # Task
    # =========================================================================

    async def create_task(
        self,
        profile: str,
        summary: str,
        due: str = "",
        description: str = "",
    ) -> dict:
        """Create a task."""
        args = ["task", "+create", "--summary", summary]
        if due:
            args.extend(["--due", due])
        if description:
            args.extend(["--description", description])
        return await self._run(args, profile)

    async def get_my_tasks(self, profile: str) -> dict:
        """List tasks assigned to current user."""
        return await self._run(["task", "+get-my-tasks"], profile)

    async def complete_task(self, profile: str, task_id: str) -> dict:
        """Mark a task as complete."""
        return await self._run(
            ["task", "+complete", "--task-id", task_id],
            profile,
        )

    # =========================================================================
    # Event Subscription (long-running)
    # =========================================================================

    async def subscribe_events(self, profile: str) -> asyncio.subprocess.Process:
        """
        Start a long-running event subscription (WebSocket + NDJSON output).

        Uses --compact for flat key-value output (one JSON per line on stdout).
        stderr is sent to DEVNULL to prevent buffer deadlock.

        Returns the Process object — caller is responsible for reading stdout
        line by line and managing the lifecycle.
        """
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "event", "+subscribe",
            "--profile", profile,
            "--compact", "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(f"Started lark-cli event +subscribe for profile {profile} (pid={proc.pid})")
        return proc
