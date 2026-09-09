"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-11-15
@description: Prompt definitions for Agent Framework (Claude Agent SDK)
"""

from collections.abc import Sequence

# ============================================================================
# Chat History Header
# Separator added when building the system prompt in agent_loop() for history records
# ============================================================================
CHAT_HISTORY_HEADER = "\n\n=== Chat History ===\n"

# ============================================================================
# Truncated Chat History Header
# Separator used in agent_loop() when the history is too long and gets truncated
# ============================================================================
CHAT_HISTORY_TRUNCATED_HEADER = "\n\n=== Chat History (truncated) ===\n"

# ============================================================================
# Chat History End Instruction
# Instruction text appended after the chat history in agent_loop()
# ============================================================================
CHAT_HISTORY_END_INSTRUCTION = "\n=== Chat History End ===\n These are the chat history between you and the user. This time please make the response by user input in this turn."

# ============================================================================
# System Prompt Truncation Warning
# Truncation notice appended when the system prompt exceeds the length limit in agent_loop()
# ============================================================================
SYSTEM_PROMPT_TRUNCATION_WARNING = "\n\n[...truncated due to length limit...]"

# ============================================================================
# Reply-surface reminder (platform-declared, per turn)
#
# The CLI has no per-step injection seam, so the closest-to-generation
# position we control is the end of the turn's user message. The tool list
# is TurnInput.expressive_tools — the same declaration NexusPower's
# per-step reply reminder consumes — rendered, never hard-coded, so both
# frameworks speak from one source of truth. The general rule stays fixed;
# only the data (which tools deliver on THIS turn's origin) varies.
# ============================================================================
REPLY_REMINDER_TEMPLATE = (
    "\n\n---\n"
    "Reminder: whoever contacted you this turn receives ONLY what you send "
    "through a reply tool ({tools}; the first is this turn's default, "
    "matching where the contact came from). A plain-text answer is never "
    "delivered to them. Reporting to your owner does not replace replying "
    "to the contact. If the message above names a specific channel or tool "
    "to answer through, that instruction outranks this list."
)


def append_reply_reminder(
    user_message: str,
    expressive_tools: "Sequence[str] | None",
    origin_declaration: str = "",
) -> str:
    """Append the origin declaration + reply-surface reminder to the turn's
    user message.

    No declaration → message untouched. That covers the one case left:
    "unknown reply surface", where inventing a tool name is worse than
    silence. Team rooms USED to land here too — their surface was
    deliberately empty because plain text auto-posted — and that exception
    is gone: a team reply is a tool call like every other, so the rule
    "plain text reaches nobody" now holds with no carve-out anywhere.

    ``origin_declaration`` is pre-rendered by the step layer
    (``render_origin_declaration``) so this function composes, never phrases.
    """
    tools = tuple(expressive_tools or ())
    if not tools:
        return user_message
    prefix = f"\n\n{origin_declaration}" if origin_declaration else ""
    return (
        user_message
        + prefix
        + REPLY_REMINDER_TEMPLATE.format(tools=", ".join(tools))
    )
