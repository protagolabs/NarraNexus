---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_trigger.py
stub: false
last_verified: 2026-06-18
---

## Why it exists

Inbound trigger for NarraMessenger built on `ChannelTriggerBase`. Structurally
a twin of `TelegramTrigger` (both are long-poll), but polling the
NarraMessenger gateway instead of Telegram `getUpdates`. The base owns dedup /
worker pool / credential watcher / audit / inbox; this subclass fills the
abstract surface and the gateway poll loop.

## Design decisions

- **`connect()` is the gateway loop**: `POST /connect` once to activate, then
  `GET /invocations/poll?timeout=30000` forever, yielding invocation dicts.
  `no_invocation` → short idle sleep; `update_guide_required` → ack the version
  programmatically (we pin the contract, we do NOT run the self-update
  document). Permanent errors (401/409 via `is_permanent_api_error`) propagate
  so the base watcher disables the credential; transient errors propagate for
  backoff/reconnect.
- **No authorize-event step.** Gateway invocations are already filtered +
  authorized by the platform, so unlike a Direct-Matrix runtime we add nothing
  to the base `_process_message` pipeline. This is why the base class needed no
  change.
- **`load_conversation_history=False`.** History rides INLINE in each
  invocation (`context` / `group_context.history_messages`); the context
  builder reads it from `ParsedMessage.raw`, so the base's per-room history
  loader is off.
- **`extract_output` scrapes `narra_send` tool-call `text`, NOT
  `output_text`** — same chain-of-thought-leak guard as Telegram/Slack.
- **`message_id` = group `trigger_message.event_id` when present, else
  `invocation_id`** — keeps the dedup key stable across any re-delivery.
- **No `fetch_attachments`** — NarraMessenger is text-only for the agent.

## Upstream / downstream

- **Upstream**: `ChannelTriggerBase`.
- **Calls**: `NarramessengerClient.connect/poll/ack_update_guide`,
  `NarramessengerCredentialManager.list_active`, `NarramessengerContextBuilder`.
- **Schemas read**: `ParsedMessage`, `ChatType`, `WorkingSource`.

## Gotchas

- DM invocations carry no timestamp; `timestamp_ms` is 0 for DMs (only group
  `trigger_message.origin_server_ts` is available). The historic-replay dedup
  baseline therefore can't gate DMs by age — the TTL window + per-invocation
  pop is the guard.
- Sender `display_name` in the payload is unreliable (often equals the
  matrix_user_id); `resolve_sender_name` deliberately returns the id rather
  than calling an API.
