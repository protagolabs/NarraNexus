"""
@file_name: _telegram_client.py
@author: NarraNexus
@date: 2026-03-29
@description: Telegram Bot API client wrapper

Wraps the Telegram Bot API via httpx.AsyncClient for use by TelegramModule.
Handles message chunking, Markdown-to-Telegram-HTML conversion, and
retry-on-parse-error logic for sendMessage.

This is a private implementation — external code should access it via TelegramModule.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from loguru import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def markdown_to_telegram_html(text: str) -> str:
    """
    Convert basic Markdown to Telegram-compatible HTML.

    Supported conversions:
    - **bold** -> <b>bold</b>
    - *italic* -> <i>italic</i>
    - `inline code` -> <code>inline code</code>
    - ```code blocks``` -> <pre>code blocks</pre>
    - [text](url) -> <a href="url">text</a>

    Order matters: process ** before * to avoid conflicts.
    On any error, return the original text unchanged.
    """
    try:
        result = text

        # Code blocks first (``` ... ```) — must come before inline code
        result = re.sub(
            r"```(?:\w*\n)?(.*?)```",
            r"<pre>\1</pre>",
            result,
            flags=re.DOTALL,
        )

        # Inline code (`...`)
        result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)

        # Bold (**...**) — must come before italic
        result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result, flags=re.DOTALL)

        # Italic (*...*)
        result = re.sub(r"\*(.+?)\*", r"<i>\1</i>", result, flags=re.DOTALL)

        # Links [text](url)
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', result)

        return result
    except Exception:
        return text


def _chunk_text(text: str, max_len: int = 4096) -> list[str]:
    """
    Split text into chunks that fit within Telegram's message size limit.

    Strategy (in order of preference):
    1. Split at paragraph boundaries (\\n\\n)
    2. Split at sentence boundaries ('. ')
    3. Hard-split at max_len
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Try paragraph boundary
        cut = remaining[:max_len].rfind("\n\n")
        if cut > 0:
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
            continue

        # Try sentence boundary
        cut = remaining[:max_len].rfind(". ")
        if cut > 0:
            chunks.append(remaining[: cut + 1])  # include the period
            remaining = remaining[cut + 2:]       # skip ". "
            continue

        # Hard-split
        chunks.append(remaining[:max_len])
        remaining = remaining[max_len:]

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TelegramBotClient:
    """
    Async wrapper around the Telegram Bot API.

    Uses httpx.AsyncClient for all HTTP communication. Each instance
    is bound to a single bot token.
    """

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    def _redact_error(self, error: Exception) -> str:
        """Redact bot token from error messages to prevent leaking in logs."""
        msg = str(error)
        if self._token and self._token in msg:
            msg = msg.replace(self._token, "***REDACTED***")
        return msg

    # ----- API methods -----

    async def get_me(self) -> dict:
        """
        Call getMe to verify the bot token and retrieve bot info.

        Returns:
            dict with bot id, is_bot, first_name, username, etc.
        """
        resp = await self._client.get(f"{self._base_url}/getMe")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getMe failed: {data}")
        return data["result"]

    async def get_updates(
        self,
        offset: int = 0,
        timeout: int = 30,
        allowed_updates: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Long-poll for new updates from Telegram.

        Args:
            offset: Identifier of the first update to be returned.
            timeout: Long-poll timeout in seconds.
            allowed_updates: List of update types to receive (e.g. ["message"]).

        Returns:
            List of Update objects.
        """
        params: dict[str, Any] = {"offset": offset, "timeout": timeout}
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates

        # Use a longer HTTP timeout than the long-poll timeout
        resp = await self._client.get(
            f"{self._base_url}/getUpdates",
            params=params,
            timeout=httpx.Timeout(timeout + 15.0, connect=10.0),
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getUpdates failed: {data}")
        return data.get("result", [])

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_to_message_id: Optional[int] = None,
        parse_mode: str = "HTML",
    ) -> dict:
        """
        Send a text message to a chat.

        If the text exceeds 4096 characters it is automatically chunked
        into multiple messages. On 400 errors mentioning "parse", the
        message is retried without parse_mode.

        Args:
            chat_id: Target chat ID.
            text: Message text.
            reply_to_message_id: Optional message ID to reply to.
            parse_mode: Parse mode ("HTML", "Markdown", or "").

        Returns:
            The last sent Message object from Telegram.
        """
        chunks = _chunk_text(text)
        last_result: dict = {}

        for i, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            # Only reply_to on the first chunk
            if reply_to_message_id and i == 0:
                payload["reply_to_message_id"] = reply_to_message_id

            last_result = await self._post_send_message(payload)

        return last_result

    async def _post_send_message(self, payload: dict[str, Any]) -> dict:
        """
        POST sendMessage with automatic retry on parse errors.

        If a 400 response mentions "parse" (e.g. bad HTML entities),
        retry the same request without parse_mode.
        """
        resp = await self._client.post(f"{self._base_url}/sendMessage", json=payload)
        data = resp.json()

        if not data.get("ok"):
            error_desc = data.get("description", "").lower()
            error_code = data.get("error_code", 0)

            # Retry without parse_mode on parse errors
            if error_code == 400 and "parse" in error_desc:
                logger.warning(
                    f"sendMessage parse error, retrying without parse_mode: {error_desc}"
                )
                payload.pop("parse_mode", None)
                resp = await self._client.post(f"{self._base_url}/sendMessage", json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"sendMessage failed after retry: {data}")
            else:
                raise RuntimeError(f"sendMessage failed: {data}")

        return data["result"]

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> bool:
        """
        Send a chat action (e.g. "typing") indicator.

        Args:
            chat_id: Target chat ID.
            action: Action type (default "typing").

        Returns:
            True on success.
        """
        resp = await self._client.post(
            f"{self._base_url}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
        )
        data = resp.json()
        return data.get("ok", False)

    async def get_chat(self, chat_id: int | str) -> dict:
        """
        Get information about a chat.

        Args:
            chat_id: Target chat ID.

        Returns:
            Chat object dict.
        """
        resp = await self._client.get(
            f"{self._base_url}/getChat",
            params={"chat_id": chat_id},
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getChat failed: {data}")
        return data["result"]

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
