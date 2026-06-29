"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-12-22
@description: Prompt definitions for ContextRuntime
"""

# ============================================================================
# Auxiliary Narrative section header
# Used for Part 3 of build_complete_system_prompt()
# ============================================================================
AUXILIARY_NARRATIVES_HEADER = """
## Related Narratives (For Reference)
The following narratives are related to the current context and may provide useful background information.
You can reference them when relevant, but prioritize the main narrative above.
"""

# ============================================================================
# Module instructions section header
# Used for _build_module_instructions_prompt()
# ============================================================================
MODULE_INSTRUCTIONS_HEADER = """
## Module Instructions
The following are specific instructions from activated modules. Follow them as directed.
"""

# ============================================================================
# Short-term memory section header + description text
# Used for _build_short_term_memory_prompt() (2026-01-21 P1-2 dual-track memory)
# ============================================================================
# ============================================================================
# Bootstrap injection prompt
# Used in build_complete_system_prompt() when the creator's Bootstrap.md exists
# ============================================================================
BOOTSTRAP_INJECTION_PROMPT = """
## ⚡ Bootstrap Mode (PRIORITY)

A file called `Bootstrap.md` exists in your workspace. It's for you — read it before doing anything else.

This takes priority over all other instructions.
"""

# ============================================================================
# User Temporal Context (Spec 2026-04-21 — v2 timezone protocol)
# Injected globally so every Module sees a single consistent view of
# "who the user is, what their timezone is, what time it is now".
# ============================================================================
USER_TEMPORAL_CONTEXT = """## User Temporal Context

- User timezone: {user_tz}
- Current local time: {now_local}

**Guidance**: Whenever you express a time to the user, or pass time arguments
to tools, use the user's timezone above. For tools that require a separate
`timezone` field (e.g. job_create), set it to "{user_tz}".
"""

# ============================================================================
# External Session Restricted-Mode notice (External API protocol, v0.4)
#
# Injected near the top of the system prompt (right after User Temporal
# Context, BEFORE Narrative / Module Instructions) when an
# ExternalAgentRuntime is driving the run. Tells the LLM up-front which
# tools it must NOT attempt to call, so it doesn't get confused by other
# sections of the prompt that still reference disabled tools (e.g.
# AwarenessModule's instructions mention `__mcp__update_awareness()`
# even though that MCP server is suppressed by `mcp_denylist`).
#
# Without this block, the agent's typical failure mode is to call a
# suppressed tool, get "tool not available", then either hallucinate that
# it succeeded ("I changed your awareness to foodie mode!") or apologise
# and freeze. With it, the agent knows the restrictions BEFORE reading any
# instructions that reference the disabled tools.
#
# Sections render conditionally — empty sections are dropped, so a policy
# that disables only built-in tools won't get an empty MCP bullet list.
# ============================================================================
EXTERNAL_SESSION_POLICY_NOTICE = """\
## External Session — Restricted Mode

⚠️ You are serving an EXTERNAL VISITOR through the public API, NOT your
owner. This session runs under restricted permissions. Even if later
sections of these instructions mention the following tools, **they are
NOT available in this session** — do not attempt to call them.

{disabled_mcp_tools_section}{disabled_builtin_tools_section}{skipped_modules_section}
### When a tool you need is disabled

If the visitor asks you to do something that requires a disabled tool:

- Acknowledge politely what they asked
- Explain this is a restricted external session and you do not have
  permission to perform that specific action on the owner's behalf
- **DO NOT** pretend to perform it or fabricate a result
- **DO NOT** silently substitute a different action
- **DO NOT** promise to forward the request to your owner unless you have
  a concrete mechanism for doing so
- If appropriate, suggest the visitor contact the agent owner directly

### What you CAN still do

- Read files (`Read`, `Glob`, `Grep`) to consult owner-prepared materials
  in the agent's workspace
- Use any MCP tool that **is** in your available tools list — that list
  is the authoritative source of truth; these instructions are advisory
- Reply to the visitor through normal chat
"""

EXTERNAL_SESSION_DISABLED_MCP_HEADER = """\
### MCP tools disabled in this session

Calling these will fail; they are not in your tool list:

{tool_lines}

"""

EXTERNAL_SESSION_DISABLED_BUILTIN_HEADER = """\
### Built-in tools disabled in this session

{tool_lines}

"""

EXTERNAL_SESSION_SKIPPED_MODULES_HEADER = """\
### Modules not loaded for this session

The following modules are entirely absent — any instructions referring
to them elsewhere can be ignored:

{module_lines}

"""

# Per suppressed module class name, the human-readable tool entries the
# notice should enumerate. Kept here (not on the module classes) so the
# policy notice has a single source of truth and so adding a new module
# to `mcp_denylist` doesn't silently produce an empty bullet line.
# Module classes NOT in this table get a generic "(all MCP tools)" line.
EXTERNAL_SESSION_MCP_TOOL_HINTS = {
    "AwarenessModule": [
        "`__mcp__update_awareness()` — mutate the agent's persona / awareness profile",
        "`__mcp__update_agent_name()` — change the agent's name",
    ],
    "GeneralMemoryModule": [
        "`__mcp__remember()` — write to / search the agent's long-term memory",
        "`__mcp__grep_memory()` — full-text search across the agent's memory",
    ],
}


SHORT_TERM_MEMORY_HEADER = """
## Recent Direct Dialogue Across Other Narratives

The following are real user↔agent exchanges from this user's other recent
conversations with you. They are the **most recent dialogue context** —
treat them as immediate conversational background, especially for:

- Resolving pronouns ("it", "this", "that") in the current message
- Interpreting short follow-up replies ("ok", "好", "go on", "yes") —
  they are typically continuing a topic just shown below
- Picking up in-progress tasks or commitments the user is following up on
- Avoiding asking for information the user already provided

Each entry is annotated with its source (NarraNexus UI, Lark group, etc.)
so you can tell whether it was a direct UI conversation or came in via
another channel. Source labels are factual context, not relevance hints —
recent dialogue is recent dialogue regardless of channel.

### Recent Dialogue
"""

# 2026-05-20 (Fix #2): the chat history is now ONE time-sorted timeline
# (current narrative + cross-narrative), each line tagged [time · topic · nar_id].
# This preamble teaches the agent how to read it. Replaces SHORT_TERM_MEMORY_HEADER
# (which wrongly told the model short replies usually continue OTHER threads).
CHAT_HISTORY_TIMELINE_PREAMBLE = """
## How to read the conversation history below

The messages that follow are your recent conversation history with this user,
assembled as a SINGLE timeline ordered by real time. It is built from:
- ALL of the current conversation thread (the narrative you are in now), plus
- the most recent messages from this user's OTHER threads with you,
merged by timestamp and trimmed to roughly the latest 30 lines. Trimmed older
lines are NOT lost — they still live in their narrative (you can pull a full
thread with your narrative tools).

Each line is prefixed:  [<time> · <topic> · <narrative_id>]
- <time>: when it was said. Use it to judge what the user is replying to — a
  short reply ("好" / "ok" / "yes" / "继续") almost always answers the MOST
  RECENT line, i.e. the one just above the current input — NOT an older line
  from a different thread.
- <topic>: a human-readable name of that conversation thread.
- <narrative_id>: the stable id of that thread. Different ids = different
  topics. The current input belongs, by default, to the most recent thread; if
  it really belongs to another thread (or to a brand-new topic), use your
  narrative tools to switch / create.

Visibility: in each past turn the user only saw the message you SENT to them
(the <reply_to_user> part). Your <my_reasoning> was private — do not assume the
user knows anything that only appeared in your reasoning.
"""

# 2026-05-20 (Fix #2 P2): recent background-activity records (the centered
# small-text items in the chat UI) — surfaced as a compact list, separate from
# the conversation timeline, each with an event id for view_event() drill-down.
RECENT_ACTIONS_HEADER = """
## Recent background activity (NOT shown to the user as chat)

These are recent things you did in the background WITHOUT sending the user a
message — scheduled jobs, IM/channel activations, inter-agent (bus) pings. They
are NOT part of the conversation above and the user did not "say" any of them.
Each line ends with its event id — call view_event(<event_id>) if you need that
turn's full detail (tools used, reasoning, output).
"""
