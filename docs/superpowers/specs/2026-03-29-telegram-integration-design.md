# Telegram Integration Design Spec

## Overview

Add Telegram as an IM channel to NarraNexus, following the MatrixModule pattern. Agents can receive messages from Telegram users, process them through AgentRuntime, and respond — both as auto-replies and via proactive MCP tool calls.

**Target UX:** OpenClaw-style Telegram bot experience (see https://docs.openclaw.ai/channels/telegram) — scoped to V1 features below.

## V1 Scope

### In scope

- Full duplex: inbound conversation + outbound notifications
- One Telegram bot token = one NarraNexus agent
- Long-polling transport (webhook-ready architecture)
- Text messages + `/start` and `/help` commands
- DM support + group chat with @mention-required activation
- Typing indicator (`sendChatAction`) during processing
- Message chunking for responses > 4096 chars (split at paragraph boundaries)
- HTML parse mode for outbound messages (plain text fallback on failure)
- Auto-reply from TelegramTrigger + agent-initiated MCP tools
- Two MCP tools: `telegram_send_message`, `telegram_reply_to_message`

### Out of scope (V2+)

- Media send/receive (images, audio, video, stickers)
- Inline keyboards / callback buttons
- Streaming preview (edit-in-place during generation)
- Pairing / sophisticated access control (V1 uses config-based allowlist)
- Forum topic routing / per-topic agent assignment
- Execution approvals via Telegram
- Webhook transport mode
- Per-group agent configuration
- Reaction support

## Architecture: Approach 1 — Full Module (Mirror MatrixModule)

TelegramModule is a first-class `XYZBaseModule` with its own MCP tools, hooks, trigger process, context builder, and DB tables. This matches the established pattern — MatrixModule is the reference implementation.

**Why not a generic IM gateway abstraction:** Premature abstraction risk. IM platforms diverge in non-obvious ways (@mention models, threading, auth, session semantics). After building Telegram (the second IM channel after Matrix), the shared patterns will emerge naturally. Extract a common layer in V2+ when we have 2-3 concrete implementations to generalize from.

## Module Structure

```
src/xyz_agent_context/module/telegram_module/
├── __init__.py
├── telegram_module.py              # XYZBaseModule subclass
├── telegram_trigger.py             # Background poller (1 Poller + N Workers)
├── telegram_context_builder.py     # ChannelContextBuilderBase subclass
├── _telegram_hooks.py              # hook_data_gathering + hook_after_event_execution
├── _telegram_mcp_tools.py          # FastMCP server: telegram_send_message, telegram_reply_to_message
├── _telegram_client.py             # Telegram Bot API HTTP wrapper
├── _telegram_credential_manager.py # Per-agent bot credential CRUD
└── _telegram_dedup.py              # Two-tier deduplication (in-memory + DB)
```

### Component Responsibilities

| Component | Does | Doesn't |
|-----------|------|---------|
| `telegram_module.py` | Registers module, wires hooks/MCP, returns instructions, registers channel sender | Know about polling or Bot API details |
| `telegram_trigger.py` | Polls for updates, batches by chat_id, invokes AgentRuntime, sends auto-reply | Know about hook execution or module lifecycle |
| `telegram_context_builder.py` | Builds channel-specific prompt (history, sender info, members, @mention context) | Call Telegram API directly |
| `_telegram_client.py` | Wraps Telegram Bot API HTTP calls (getUpdates, sendMessage, sendChatAction, getMe, getChat, getChatMember) | Manage credentials or state |
| `_telegram_credential_manager.py` | Stores/retrieves per-agent bot token + metadata in MySQL | Call Telegram API |
| `_telegram_mcp_tools.py` | Exposes agent-callable tools for proactive messaging | Handle inbound messages |
| `_telegram_dedup.py` | Prevents duplicate processing of the same update_id | Know about message content |

## Message Flow: Inbound

```
Telegram Cloud
    │
    ▼
TelegramTrigger (background process)
    │  long-polls getUpdates(timeout=30)
    │  returns instantly on new message, otherwise waits up to 30s
    ▼
Filter & Classify
    │  • Ignore non-message updates (edited_message, channel_post, etc.)
    │  • DM → always process
    │  • Group → check @bot_username mention, ignore if not mentioned
    │  • Handle /start and /help commands directly (no AgentRuntime needed)
    ▼
Batch by chat_id
    │  • Multiple messages from same chat → take latest as trigger
    │  • Record all update_ids for dedup
    ▼
Dedup Check
    │  • In-memory set (hot, fast)
    │  • DB table telegram_processed_updates (cold, survives restart)
    │  • Skip if already processed
    ▼
Worker Queue (asyncio.Queue)
    │  • N concurrent workers (configurable, default 3)
    ▼
Worker picks up task
    │
    ├─► sendChatAction("typing") to originating chat
    │
    ├─► Build ChannelTag.telegram(sender_name, sender_id, chat_id, chat_title)
    │
    ├─► Build prompt via TelegramContextBuilder
    │     • Message metadata (chat type, sender info)
    │     • Conversation history (last K messages via Telegram API or DB cache)
    │     • Current message body
    │     • Chat members (for group chats)
    │     • Action instructions
    │
    ├─► Call AgentRuntime.run(
    │       agent_id=credential.agent_id,
    │       input_content=prompt,
    │       working_source=WorkingSource.TELEGRAM,
    │       trigger_extra_data={"channel_tag": tag, "telegram_chat_id": chat_id}
    │   )
    │
    ├─► Collect agent response (stream AgentTextDelta → accumulate final text)
    │
    ├─► Send reply via _telegram_client.send_message(chat_id, text, reply_to_message_id, parse_mode="HTML")
    │     • Chunk if > 4096 chars (split at paragraph boundaries)
    │     • Fallback to plain text if HTML parse fails
    │
    └─► Mark update_ids as processed (memory + DB)
```

### Long-polling model

`getUpdates(timeout=30)` uses Telegram's built-in long-polling: the HTTP request blocks until a new update arrives or the timeout expires. Effective latency is sub-second for new messages. Polling never blocks on agent processing — the poller and workers run as independent coroutines.

When no agents have active Telegram credentials, the poller checks for new credentials every 15-30s rather than calling the Telegram API.

### Safety mechanisms

- **Per-chat batching:** Multiple messages from same chat collapse into one AgentRuntime call
- **Rate limiting:** Max 20 triggers per agent per chat per 30-minute window (matches MatrixTrigger constants)
- **Dedup TTL:** 7-day retention in DB, hourly cleanup of expired rows

## Message Flow: Outbound

### Path 1: Auto-reply (TelegramTrigger)

When AgentRuntime finishes processing an inbound message, the trigger automatically sends the response to the originating chat with `reply_to_message_id` set to the triggering message. HTML parse mode, plain text fallback.

### Path 2: MCP Tools (Agent-Initiated)

During reasoning, the agent can proactively send messages via MCP tools.

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `telegram_send_message` | `chat_id`, `text`, `parse_mode` (optional, default HTML) | Send to any accessible chat |
| `telegram_reply_to_message` | `chat_id`, `message_id`, `text`, `parse_mode` | Reply to a specific message |

### Message formatting

Outbound messages use HTML parse mode. Agent Markdown output is converted to Telegram-compatible HTML via a `markdown_to_telegram_html(text) -> str` helper in `_telegram_client.py`. If HTML parsing fails (Telegram returns 400), retry as plain text. This matches OpenClaw's approach — HTML is more forgiving than Telegram's MarkdownV2.

### Message chunking

Responses exceeding 4096 characters are split at paragraph boundaries (double newline). If no paragraph boundary exists within the limit, split at the last sentence boundary. Each chunk is sent as a separate message.

## Data Model

### `telegram_credentials` table

Mirrors `matrix_credentials`. One row per agent.

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGINT UNSIGNED PK AUTO_INCREMENT | |
| `agent_id` | VARCHAR(64) UNIQUE | Agent ID |
| `bot_token` | VARCHAR(256) NOT NULL | Telegram bot token |
| `bot_username` | VARCHAR(128) | Cached from `getMe` |
| `bot_id` | BIGINT | Telegram bot user ID |
| `allowed_user_ids` | JSON NULL | Per-agent allowlist of Telegram user IDs (overrides global default) |
| `is_active` | TINYINT(1) DEFAULT 1 | Whether polling is enabled |
| `created_at` | DATETIME(6) | Auto |
| `updated_at` | DATETIME(6) | Auto on update |

### `telegram_processed_updates` table

Mirrors `matrix_processed_events`. Persistent dedup layer.

| Column | Type | Description |
|--------|------|-------------|
| `update_id` | BIGINT NOT NULL | Telegram update_id |
| `agent_id` | VARCHAR(64) NOT NULL | Agent that processed this |
| `processed_at` | DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) | |
| PK | `(update_id, agent_id)` | Composite |
| Index | `idx_processed_at` | For TTL cleanup |

7-day retention, hourly cleanup — same as Matrix.

### Identity mapping

Telegram users map to NarraNexus via `ChannelTag`:
- `channel`: `"telegram"`
- `sender_id`: Telegram user ID (numeric, as string)
- `sender_name`: `first_name` + `last_name`
- `room_id`: Telegram chat ID (as string)
- `room_name`: Chat title (groups) or user display name (DMs)

No separate user mapping table. SocialNetwork module's entity extraction discovers Telegram users automatically via its existing `hook_after_event_execution`.

## Configuration

### settings.py addition

```python
telegram_bot_token: str = ""
```

Global default token. Per-agent tokens stored in `telegram_credentials` DB table.

### .env.example addition

```bash
# Telegram Bot (optional — enables Telegram channel)
TELEGRAM_BOT_TOKEN=""
# Comma-separated Telegram user IDs allowed to interact (empty = allow all)
TELEGRAM_ALLOWED_USER_IDS=""
```

### Credential flow (V1: one bot = one agent)

1. User sets `TELEGRAM_BOT_TOKEN` in `.env`
2. On first run, TelegramModule creates a `telegram_credentials` row for the agent
3. TelegramTrigger reads credentials from DB (not directly from settings)

## Framework Integration Points

### WorkingSource enum (`schema/hook_schema.py`)

```python
TELEGRAM = "telegram"  # Added to enum

def is_automated(self) -> bool:
    return self in (..., WorkingSource.TELEGRAM)
```

### ChannelTag factory (`schema/channel_tag.py`)

```python
@classmethod
def telegram(cls, sender_name, sender_id, chat_id, chat_title="") -> "ChannelTag":
    return cls(channel="telegram", sender_name=sender_name, sender_id=sender_id,
               room_id=chat_id, room_name=chat_title)
```

### MODULE_MAP (`module/__init__.py`)

```python
"TelegramModule": TelegramModule
```

### MCP port (`module/module_runner.py`)

```python
"TelegramModule": 7812
```

### Start script (`start/telegram-trigger.sh`)

Same retry/backoff/signal-trap pattern as `start/matrix-trigger.sh`. Command:

```bash
uv run python -m xyz_agent_context.module.telegram_module.telegram_trigger
```

New tmux window added in `start/all.sh`.

### Table management scripts (`utils/database_table_management/`)

- `create_telegram_credentials_table.py`
- `create_telegram_processed_updates_table.py`

Registered in `create_all_tables.py` and discoverable by `sync_all_tables.py`.

## Testing Strategy

### Import validation

```bash
uv run python -c "import xyz_agent_context.module.telegram_module; print('OK')"
```

### Unit tests (`tests/test_telegram_module/`)

| Test file | Coverage |
|-----------|----------|
| `test_telegram_client.py` | Bot API wrapper: mock HTTP responses, request formatting, HTML conversion, chunking |
| `test_telegram_dedup.py` | In-memory + DB dedup: insert, check, expiry, cleanup |
| `test_telegram_credential_manager.py` | CRUD against test DB |
| `test_telegram_context_builder.py` | Prompt assembly: DM vs group, @mention detection, history formatting |
| `test_telegram_trigger.py` | Update filtering, chat_id batching, @mention filtering, dedup skipping (mock client + runtime) |

### Manual smoke test

Requires real bot token. Set `TELEGRAM_BOT_TOKEN` in `.env`, start all services, send messages in Telegram. Verify agent logs + Telegram responses.

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Full module vs lightweight adapter | Full module | Architectural consistency, MCP tools for outbound, hook participation |
| Full module vs generic IM gateway | Full module | Premature abstraction risk; extract gateway after 2-3 concrete channels |
| Transport | Long-polling (webhook-ready) | Matches MatrixTrigger pattern, no public URL needed, sub-second latency via Telegram long-poll |
| Outbound format | HTML with plain text fallback | More reliable than MarkdownV2; matches OpenClaw approach |
| Group activation | @mention required | Standard Telegram bot UX; users already understand it |
| Media support | Deferred to V2 | Significant scope (file handling, storage, content routing); text covers 90% of use cases |
| Streaming preview | Deferred to V2 | Complex (partial buffering + edit API); auto-reply is sufficient for V1 |
| Access control | Config-based allowlist (V1) | OpenClaw's pairing system is V2; simple allowlist is enough to start |

## Resolved Questions

1. **Conversation history source:** No new table needed. ChatModule's `hook_after_event_execution` already saves every conversation (user input + assistant response) to EventMemoryModule, tagged with channel_tag. On subsequent triggers, ChatModule's `hook_data_gathering` loads history via dual-track memory (long-term EverMemOS episodes + short-term cross-Narrative messages). TelegramContextBuilder only needs to provide immediate context (current message metadata, sender info, chat members for groups) — deep history is ChatModule's responsibility. This differs from MatrixContextBuilder which fetches history from the Matrix server API, because Telegram's Bot API has no "get chat history" endpoint for bots.

2. **Bot username in @mention detection:** Cached from `getMe` on credential creation only. Bot usernames rarely change. Stored in `telegram_credentials.bot_username`.

3. **Allowlist granularity:** Per-agent, stored as a JSON array column (`allowed_user_ids`) in `telegram_credentials` table, with the global `.env` value (`TELEGRAM_ALLOWED_USER_IDS`) as default when the per-agent field is empty.
