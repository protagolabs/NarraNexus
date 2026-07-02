---
code_file: src/xyz_agent_context/module/narramessenger_module/matrix_trigger.py
stub: false
last_verified: 2026-07-02
---

## 2026-07-02 (Commit 5) — Narra authorize-event gate

Every Matrix event must clear
`POST {backend_base_url}/api/agent-runtime/matrix/authorize-event`
BEFORE we read history, write memory, invoke tools, call the model,
or send a Matrix reply. Applies to **both** silent and full paths —
silent still writes chat_history + observations, which the guide
explicitly lists as gated operations.

Implementation (`_authorize_event` + `_send_matrix_notice`):

- Uses the **Narra agent secret token** (`credential.bearer_token`), NOT
  the Matrix access token. These are different secrets with
  different scopes; the Matrix access token would return `401` from
  this endpoint.
- Fails **closed** on: non-2xx status, transport exception / timeout,
  invalid JSON body, `allow` field missing or not exactly `True`, or
  credentials missing `bearer_token` / `backend_base_url`. All → drop
  the event without further processing.
- 401 during pending bind is expected fail-closed behaviour (owner
  has not called `runtime-ready` yet). Logged at INFO, not WARNING,
  so it does not spam ERROR during the normal bind-in-progress
  window. It is NOT wired into `is_permanent_auth_failure` — that
  method only catches Matrix `M_*` codes, so a Narra 401 correctly
  will not disable the Matrix credential.
- On `allow != true` with `notice.send == true`, we forward exactly
  `notice.text` back to the same room as `m.notice`. This is the only
  side effect allowed for a denied event; no memory write, no tool
  call, no model call, no other reply. If `notice.send != true`, we
  drop silently.

Mention detection was split out of `_classify` into
`_is_mentioning_us` (Commit 5) so the gate can compute the
`mentioned` payload flag BEFORE classification runs, and `_classify`
reuses the value via a `mentioned=` kwarg — no double body scan.

## Why it exists

Phase 1 replacement for the polling long-poll on the NarraMessenger
message plane. NarraMessenger's backend runs a Matrix homeserver
(`matrix.netmind.chat`); this trigger is a `matrix-nio` client that
opens `/sync`, receives events causally, and posts replies back via
`room_send`. We host **zero** Matrix infrastructure — homeserver,
TLS, federation, storage, backups all belong to the NarraMessenger
team. The trigger runs in-process next to
[[narramessenger_trigger]] (polling), each subclass filters
credentials by `connection_mode` so both transports can coexist on
the same agent set during migration.

## Design decisions

- **Distinct `channel_name`**. `narramessenger_matrix` partitions the
  base's dedup store, audit log, and per-channel stats from the
  legacy polling trigger. Matrix `event_id` and NarraMessenger
  `invocation_id` live in different ID spaces and must not collide
  in one dedup bucket.
- **`working_source` stays `NARRAMESSENGER`**. Downstream of
  `parse_event` (context builder, module coordination, reply routing)
  treats Matrix and polling as the same product surface — the
  transport swap is invisible to Modules.
- **Non-`[e2e]` matrix-nio**. `matrix.netmind.chat` rooms are
  plaintext by default (verified empirically 2026-07-02 — new rooms
  have no `m.room.encryption` state event). Skipping libolm removes a
  native-C dependency and shaves ~30 MB from the runtime footprint.
  If NM ever flips a room to encrypted, `client.sync` will surface
  `EncryptedEvent` instances that we currently drop in `_wrap_event`
  — the failure mode is "agent stops seeing messages in that room",
  not "agent crashes". Phase 3+ concern.
- **Silent-batch buffering for group non-@ messages**. Group messages
  that don't mention the agent should still land in memory
  (chat_history, observations, entity descriptions) but MUST NOT run
  the agent LLM — that's the whole point of the memory-only path
  (see [[agent_runtime.py]] `silent=True` and
  [[channel_trigger_base]] `_build_and_run_agent_silent_batch`).
  Per-room debounce (5s idle) coalesces bursts into one silent-batch
  runtime call; the burst cap (20 msgs) forces flush on active
  channels so memory lag stays bounded.
- **Reply via `client.room_send`**. Unlike polling, there is NO 15-
  minute response-timeout ceiling — Matrix decouples message delivery
  from a synchronous request/response window. The agent can run for
  hours; when it finishes, one HTTP POST hands the reply to the
  homeserver, which fans out to every joined client (including the
  user's mobile app opened days later).
- **`extract_output` reads `send_message_to_user_directly`**. Matches
  the discipline of Lark / Slack / Telegram: only content the agent
  explicitly passed to the reply tool ends up in the room. Never
  falls back to `result.output_text` — that's the agent's internal
  reasoning stream, and posting it as a reply would leak
  chain-of-thought into a user-visible channel.

## The three-way classification

`_classify(client, message, credential)` returns one of:

- `"dm"` — 2 members in the room. Full agent-loop path via
  `_process_message` → `_build_and_run_agent` → reply.
- `"group_mention"` — 3+ members AND agent explicitly mentioned. Same
  as DM. Mention detection covers (1) MSC3952 intentional mentions
  (`m.mentions.user_ids`), (2) raw MXID inline, and (3) `@displayname`
  inline — the three surfaces different clients emit.
- `"group_silent"` — 3+ members AND no mention. Route to per-(agent,
  room) buffer, no agent LLM invocation. Flushed via debounce OR
  burst-cap OR reconnect-burst drain OR `stop()` drain.

## Cursor-save ordering (crash-safe restart)

The sync loop follows a strict ordering:

```
resp = await client.sync(since=X, timeout=30s)
for room in resp.rooms.join:
    apply member state events        # populate caches BEFORE classification
    for message event in timeline:
        yield event                    # base awaits _dedup_and_handle inline
if self._silent_buffer:
    await drain_all_silent_buffers    # backlog flush BEFORE cursor commit
update_since_token(next_batch)         # ONLY after all writes durable
```

If the process dies mid-batch, the cursor stays at `X`, the server
replays the same batch on next connect, and the base's event_id
dedup filters the already-handled messages. Persisting the cursor
before drain would lose the silent memory writes.

## Reply send retry policy

`_send_matrix_reply` handles the three real failure modes:

- `M_LIMIT_EXCEEDED` → honor `retry_after_ms`, retry. Not counted
  against `SEND_MAX_ATTEMPTS`.
- `M_UNKNOWN_TOKEN` / `M_MISSING_TOKEN` / `M_FORBIDDEN` → give up on
  the FIRST hit; the base's sync loop will trip
  `is_permanent_auth_failure` on its next `/sync` call and disable
  the credential. Do not burn 3 attempts against a dead token.
- Transient (network / 5xx / unknown status) → exponential backoff,
  cap `SEND_MAX_ATTEMPTS`.

Any failure that exhausts the retry budget writes an audit row of
type `transport_send_failed` (added to
`channel_audit_events.py` in the same commit) with `error_code`,
`attempts`, and a truncated `body_preview`. That's the only signal
owner-notification tooling can key on, so it MUST be written even
for permanent auth failures.

## What silent path DOES NOT send

The silent-batch runtime call fabricates an empty `PathExecutionResult`
(see [[agent_runtime.py]] silent-mode note), so `_build_and_run_agent`
is not on the path at all — no `_send_matrix_reply` call ever fires
for silent messages. The room never sees a stray "still thinking…"
or empty message from the agent while we're silently ingesting a
group thread.

## Related

- [[channel_trigger_base]] — base class; `_build_and_run_agent_silent_batch`
  is the shared silent-mode entry point.
- [[agent_runtime.py]] — `silent=True` runtime mode.
- [[chat_module.py]] — silent-batch write path in `hook_persist_turn`.
- [[narramessenger_trigger]] — polling transport, coexists with this.
- [[_narramessenger_credential_manager]] — where matrix creds live
  (`connection_mode='matrix'` rows).
