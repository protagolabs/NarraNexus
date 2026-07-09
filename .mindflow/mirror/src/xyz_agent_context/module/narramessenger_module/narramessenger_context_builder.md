---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_context_builder.py
stub: false
last_verified: 2026-07-09
---

## 2026-07-09 — reply_instruction drops the `narra_progress` clause

Companion cleanup to the silent-first refactor on [[matrix_trigger.py]]
(2026-07-08). The `reply_instruction` string used to include:

> "For genuinely long work you MAY call `narra_progress(text="…")` first
> with a few-word status — it updates the sender's 'thinking' message in
> place."

That whole clause is gone. There is no thinking placeholder anymore —
the room stays quiet until `narra_reply` fires — so telling the agent
otherwise burned a tool call, spent tokens, and let the model believe
it had already communicated a status to the sender. Prompt now tells
the agent explicitly: *the room stays quiet until you call
`narra_reply`; if the work takes a while, just do it and send the
final answer when ready*.

The `narra_progress` tool itself was removed in the same PR (see
[[_narramessenger_mcp_tools]] 2026-07-09 section).

## 2026-07-03 — reply_instruction points at `narra_reply`

`reply_instruction` / `send_tool_name` changed from `narra_send` to
**`narra_reply`** (+ a line about `narra_send_media` for attachments). This is
what tells the agent to use the trigger-delivered reply marker instead of the
old Gateway `/chat/send` tool — the prompt half of the send unification (see
[[_narramessenger_mcp_tools]] / [[matrix_trigger.py]]).

## Why it exists

Assembles the per-turn execution prompt for a NarraMessenger message via the
`ChannelContextBuilderBase` Template Method. Unlike Telegram (which keeps its
own `bus_messages` history), NarraMessenger ships conversation context INLINE
in every invocation, so this builder reads straight from `ParsedMessage.raw`.

## Design decisions

- **`get_conversation_history` reads the invocation payload**: group →
  `group_context.history_messages`; DM → `context` (`[{role, sender, content}]`).
  Either is normalised to `[{sender, timestamp, body}]`. A trailing entry that
  duplicates the current trigger message is dropped (the template renders the
  current message separately).
- **`get_room_members` reads `group_context.members`** (matrix_user_id +
  display_name). DM → empty (base hides the members section for ≤2 anyway).
- **`send_tool_name="narra_send"`** + a `reply_instruction` telling the agent
  to call `narra_send(room_id=..., text=...)`.
- **`my_channel_id = credential.matrix_user_id`** so "Me (agent)" rows in
  history are labelled correctly.

## Upstream / downstream

- **Upstream**: `ChannelContextBuilderBase` (`build_prompt` / `build_retrieval_anchor`).
- **Reads**: `ParsedMessage.raw` (the full invocation), the credential's
  `matrix_user_id`.

## Gotchas

- History fidelity depends entirely on what the platform put in `context` /
  `group_context`. `compressed_context` may be `null` and `context_state` may
  flag gaps — for v1 we use whatever is provided and do not call
  `/agent-context/group` separately (gateway already inlines it).
