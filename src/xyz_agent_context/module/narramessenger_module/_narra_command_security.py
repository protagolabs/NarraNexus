"""
@file_name: _narra_command_security.py
@date: 2026-07-20
@description: Security layer for the generic ``narra_cli`` passthrough MCP tool.

The passthrough hands an arbitrary command string to the local ``narra-cli``
binary. This module keeps that safe with a small, explicit surface (narra-cli
has only ~6 domains):

  - ``ALLOWED_DOMAINS`` whitelist — the first token must be a known narra-cli
    domain.
  - ``BLOCKED_PATTERNS`` — ``configure`` (endpoint is a platform-global concern)
    and ``doctor`` (a probe surface); these are not the agent's to run.
  - ``BLOCKED_FLAGS`` — ``--token`` / ``--token-file`` are INJECTED by
    ``narra_cli_client`` per call; an agent supplying its own would override our
    injection or probe for a readable path, so they are always rejected.
  - ``explore`` is gated to official agents (upstream marks it "Official Agents
    Only").
  - ``shlex.split`` + ``shell=False`` argv is the real injection defense — NOT a
    shell-metachar denylist. A denylist would only break legitimate message
    content ("S&P 500", "$76,000", markdown tables) while adding no safety under
    ``execve`` (the same lesson burned in on the Lark side —
    ``_lark_command_security``).

Mirrors ``lark_module/_lark_command_security.py`` in shape; independent per
binding rule #3 (modules must not import each other).
"""

from __future__ import annotations

import shlex
from typing import Tuple

# Allowed top-level command domains (first token of a narra-cli command).
# Derived from the runtime guide's command surface.
ALLOWED_DOMAINS = {
    "room",
    "im",
    "speech",
    "explore",   # gated to official agents — see validate_command
    "status",
    "help",
}

# Blocked commands (prefix match against the full command string).
#   configure — endpoint is a platform-global concern (run.sh / configure once)
#   doctor    — diagnostic probe surface; not the agent's to run
#   im send   — TRANSITIONAL: sending stays on the Matrix-direct dedicated tools
#               (narra_reply / narra_send / narra_send_media) while the proxy
#               media path (moderation / compound / failure modes) is validated
#               on dev. narra_cli's ``im`` is for messages / attachments only.
#               Remove this block (and the dedicated send tools) once the proxy
#               send/media path is verified. ``im messages`` / ``im attachments``
#               are NOT matched by this prefix and remain allowed.
BLOCKED_PATTERNS = [
    "configure",
    "doctor",
    "im send",
]

# Flags the platform INJECTS per call — the agent must never supply them.
BLOCKED_FLAGS = [
    "--token",
    "--token-file",
]

_ESCAPE_MAP = {
    r"\n": "\n",
    r"\t": "\t",
    r"\r": "\r",
}


def _expand_escapes(value: str) -> str:
    """Convert literal ``\\n`` / ``\\t`` / ``\\r`` to real chars.

    LLMs compose command strings and naturally write ``\\n`` to mean a newline,
    but ``shlex.split`` preserves the backslash literally — so ``--text "a\\nb"``
    would reach narra-cli as the 4-char string ``a\\nb`` and render a literal
    ``\\n`` instead of a line break.
    """
    for esc, real in _ESCAPE_MAP.items():
        value = value.replace(esc, real)
    return value


def validate_command(command: str, *, is_official: bool = False) -> Tuple[bool, str]:
    """Validate a narra-cli command string.

    Args:
        command: the raw command (without the ``narra-cli`` prefix), e.g.
            ``im send --room-id !r:h --text hi``.
        is_official: whether this agent is a Narra "official" agent (only such
            agents may use the ``explore`` timeline domain).

    Returns:
        ``(True, "")`` if allowed, else ``(False, reason)``.
    """
    if not command or not command.strip():
        return False, "Empty command"

    stripped = command.strip()
    lower = stripped.lower()

    # Blocked top-level patterns.
    for pattern in BLOCKED_PATTERNS:
        if lower == pattern or lower.startswith(f"{pattern} "):
            return False, (
                f"Blocked command: '{pattern}' — not available via narra_cli"
            )

    # Injected flags must never come from the agent.
    tokens = stripped.split()
    for flag in BLOCKED_FLAGS:
        if flag in tokens or any(t.startswith(f"{flag}=") for t in tokens):
            return False, (
                f"Blocked flag: '{flag}' — the platform injects the agent token; "
                "do not pass it yourself"
            )

    # Domain whitelist.
    domain = tokens[0].lower()
    if domain not in ALLOWED_DOMAINS:
        return False, (
            f"Unknown command domain: '{domain}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_DOMAINS))}"
        )

    # explore is official-agents-only.
    if domain == "explore" and not is_official:
        return False, (
            "'explore' (public timeline) is available to official agents only"
        )

    return True, ""


def sanitize_command(command: str, *, is_official: bool = False) -> list[str]:
    """Validate, then parse a command string into a safe argv list.

    ``shlex.split`` (proper quote handling) + ``\\n\\t\\r`` escape expansion, then
    handed to ``create_subprocess_exec`` with ``shell=False`` — no character-level
    stripping is needed or wanted (see module docstring).

    Raises:
        ValueError: if the command is blocked, or shlex cannot parse it.
    """
    allowed, reason = validate_command(command, is_official=is_official)
    if not allowed:
        raise ValueError(reason)

    try:
        args = shlex.split(command.strip())
    except ValueError as e:
        raise ValueError(f"Failed to parse command: {e}")

    return [_expand_escapes(a) for a in args]
