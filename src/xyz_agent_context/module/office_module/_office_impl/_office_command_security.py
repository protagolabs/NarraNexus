"""
@file_name: _office_command_security.py
@author: rujing.yan
@date: 2026-07-13
@description: Parse + gate raw officecli command strings for the passthrough tool.

The agent drives officecli through a single ``office_cli(command)`` passthrough
(same shape as lark's ``lark_cli``). We shlex-split into an argv array and run it
with ``shell=False`` — that array form is the real defence against shell
injection. On top of that we block a small set of subcommands that either mutate
global/system state or start a long-running server (which would just block until
the subprocess timeout):

- ``install`` / ``config``  — global binary/config mutation, not a doc op.
- ``mcp``                   — starts an MCP server (blocks).
- ``watch``                 — starts a live-preview HTTP server (blocks). Agents
                              should use ``office_render`` for a static preview.

Everything else (create / view / get / query / set / add / remove / move / swap /
validate / batch / dump / refresh / merge / raw* / open / close / save, and the
``docx|xlsx|pptx <verb>`` format-prefixed forms) is allowed.
"""

from __future__ import annotations

import shlex

_BLOCKED_SUBCOMMANDS = frozenset({"install", "config", "mcp", "watch"})


def validate_command(command: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a raw officecli command string.

    ``reason`` is empty when allowed, else a caller-readable rejection message.
    """
    stripped = (command or "").strip()
    if not stripped:
        return False, "empty command"

    try:
        tokens = shlex.split(stripped)
    except ValueError as e:
        return False, f"could not parse command: {e}"
    if not tokens:
        return False, "empty command"

    # The subcommand is the first non-flag token (skip any leading global flags).
    subcommand = next((t for t in tokens if not t.startswith("-")), "")
    if subcommand in _BLOCKED_SUBCOMMANDS:
        return False, (
            f"'{subcommand}' is not allowed from office_cli. "
            f"Blocked: {', '.join(sorted(_BLOCKED_SUBCOMMANDS))}. "
            f"Use office_render for a preview instead of 'watch'."
        )
    return True, ""


def sanitize_command(command: str) -> list[str]:
    """Validate then shlex-split ``command`` into a safe argv list (no 'officecli').

    Raises ValueError if the command is blocked or unparseable.
    """
    allowed, reason = validate_command(command)
    if not allowed:
        raise ValueError(reason)
    return shlex.split(command.strip())
