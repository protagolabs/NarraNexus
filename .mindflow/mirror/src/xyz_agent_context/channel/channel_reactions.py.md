---
code_file: src/xyz_agent_context/channel/channel_reactions.py
stub: false
last_verified: 2026-07-10
---

## Why it exists

Extracted 2026-07-10 to kill a 5-way copy-paste flagged in PR #87 review: every
IM module's `react_to_user_message` tool had the same body, and every IM
channel's `get_instructions` had the same "ack early" directive + the same
hardcoded 11-name emoji menu. Adding a mood or fixing wording meant editing 10
places; drift had already started.

Lives in the `channel/` package — a **legal shared dependency of every module**
(same seam as `channel_trigger_base`, which `lark_trigger` already imports), so
this does NOT violate rule #3 (modules never import each other). The only thing
that legitimately varies per platform — the semantic→token map — stays in each
module.

## What's here

- **`REACTION_VOCABULARY`** — the single source of truth for the 11 semantic
  "task mood" names the agent picks from. `reaction_menu()` renders it into the
  prompt. Add a mood here + one line per module map; prompt + tool update
  automatically.
- **`best_effort_react(mapping, emoji, react, *, log_label)`** — the shared
  react-tool body: resolve semantic→token (unknown → `on_it`), call the
  channel's `react(token)` coro, return a `{success, ...}` envelope. **Never
  raises** and **logs** the failure (lesson #3: don't silently swallow) so a
  missing scope / deleted message can't break the agent turn but is still
  visible in logs. Telegram's tool wraps its bool-returning
  `set_message_reaction` in a coro that raises on `False` so this path reports it.
- **`render_early_feedback(*, tool_ref, room_id, message_id, inline)`** — the
  generic "ACK FIRST for non-trivial requests" directive for a channel's
  `get_instructions`. `tool_ref=None` → message-only ack (WeChat, no reaction
  API). `inline=True` → Lark's `**Early feedback**: …` line; else a
  `### Early feedback` section. The full-qualified-vs-bare tool name is the
  caller's choice (Lark passes `mcp__lark_module__…` to match its convention).

## Consumers

`_{lark,slack,discord,telegram}_mcp_tools`'s `react_to_user_message` (via
`best_effort_react`); `{lark,slack,discord,telegram,wechat}_module.get_instructions`
(via `render_early_feedback`).
