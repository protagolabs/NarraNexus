---
code_file: src/xyz_agent_context/module/lark_module/lark_context_builder.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Builds execution context for Lark-triggered messages by implementing
`ChannelContextBuilderBase`.  Fetches conversation history and maps
Lark-specific fields to the normalized format the runtime expects.

## Design decisions

- **Inherits `ChannelContextBuilderBase`** — same pattern as other
  channel integrations (e.g., LINE, Discord).  Implements three
  methods: `get_message_info`, `get_conversation_history`,
  `get_room_members`.
- **`get_room_members` returns `[]`** — Lark CLI has no
  `+chat-members` shortcut yet.  Can be implemented via the API layer
  when needed.
- **Content JSON unwrapping** — Lark CLI may return message content
  as a JSON string `{"text": "hi"}`; the builder extracts the `text`
  field transparently.

## Upstream / downstream

- **Upstream**: `lark_trigger.py` (`_build_and_run_agent`).
- **Downstream**: `LarkCLIClient.list_chat_messages`,
  `ChannelContextBuilderBase.build_prompt`.

## Gotchas

- **`send_tool_name` is `"lark_cli"`** (V2). The base class template
  uses `{reply_instruction}` which this builder sets to a specific
  `lark_cli` call example. If you change the V2 tool signature, update
  the `reply_instruction` string here.
- **`reply_instruction` override** — unlike other channels that use the
  default `"use the {tool} tool with room_id={id}"`, Lark provides
  an explicit CLI command example because `lark_cli` takes a command
  string, not structured parameters.
