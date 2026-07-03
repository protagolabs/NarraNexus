---
code_file: src/xyz_agent_context/module/narramessenger_module/matrix_trigger.py
stub: false
last_verified: 2026-07-03
---

## 2026-07-03 (Phase 3) — multimodal ingest (m.image / m.file / m.audio / m.video)

Promoted from the "Deferred to Phase 3" stub in the file docstring to a
working receive path. Diagnosed from a live report: a message carrying a
picture produced no agent reaction. Root cause was **not** a parse gap —
`_wrap_event` only recognised `RoomMessageText`, so every media event hit
the `else` branch and was dropped with a `skipping event type=…` debug
line **before** `parse_event` or any download could run. Three layers were
text-only; all three now handle media, mirroring
[[channel_trigger_base]]'s attachment contract that Lark / Slack /
Telegram / Discord already use.

Flow (one `RoomMessageMedia` — the shared base of Image/File/Audio/Video):

1. **`_wrap_event`** — `isinstance(event, RoomMessageMedia)` marshals the
   mxc URI (`event.url`), filename (`event.body`), and the `content.info`
   block (`mimetype` / `size`) from `event.source` into a
   `kind="m.room.message.media"` raw dict. mimetype is only a hint —
   `_persist_attachment` re-sniffs the real MIME from bytes.
2. **`parse_event`** — media kind → one `attachment_refs` entry
   (`mxc_url` / `original_name` / `mime_hint` / `size_hint`) + coarse
   `content_type` (msgtype first, MIME family fallback). `content=""`
   (the filename is the attachment's `original_name`, NOT message text),
   so a caption-less image still flows — the base guard passes because
   `attachment_refs` is non-empty. The raw dict is **copied** before
   adding refs so the base's dedup key (which still holds the original)
   is untouched.
3. **`fetch_attachments`** (override) — for each ref: parse
   `mxc://{server}/{media_id}`, size pre-check vs
   `backend_settings.max_upload_bytes`, `_download_mxc` → base
   `_persist_attachment` (workspace store + MIME sniff + audio STT →
   `Attachment`). Never raises; audits `EVENT_INGRESS_DROPPED_OVERSIZED`
   / `EVENT_ATTACHMENT_FETCH_FAILED` / `EVENT_ATTACHMENT_PERSISTED`.
   Structure is a near-copy of `DiscordTrigger.fetch_attachments`; the
   **only** channel-specific part is the download source.
4. **`_download_mxc`** — the single network seam (tests monkeypatch it).
   `GET {homeserver}/_matrix/client/v1/media/download/{server}/{media_id}`
   with `Authorization: Bearer {matrix_access_token}` (the **Matrix**
   token, not the Narra bearer). The legacy unauthenticated
   `/_matrix/media/r0/download` path is gone on `matrix.netmind.chat`
   (verified 2026-06-30). Streams with a `max_bytes` cap so a lying
   `size_hint` can't blow memory; over-cap mid-stream →
   `MatrixMediaError("oversized")`, which the caller audits as OVERSIZED
   (not FETCH_FAILED) via the `code` split — same idea as
   `DiscordSDKError.code`.

**Why the prompt "download location" is free (design note the owner
called out):** we deliberately route through `_persist_attachment`
instead of storing bytes ourselves. That helper generates the `att_`
file_id and writes under the WS-upload path, so
`Attachment.synthesize_marker` (ChatModule) later emits
`[User uploaded image: … path=<abs> … — use Read tool to view]` with a
path `resolve_attachment_path` can actually resolve. Storing to a
self-invented path/file_id would make the marker render `<unavailable>`
and the agent could not open the file. The test asserts this end-to-end
(marker path == `resolve_attachment_path` == on-disk bytes).

Sending images back (agent-generated) is still out of scope this version
(§8.2 of the spec). Encrypted media (`EncryptedEvent`) is still dropped —
rooms are plaintext by default; that remains the separate Phase 3+ item
noted below.

Tests: `tests/narramessenger_module/test_matrix_attachment_ingest.py`.

## 2026-07-02 (owner override) — SILENT_BYPASS_AUTHORIZE

Live E2E confirmed the diagnosis from the INFO-log hotfix: Narra's
`authorize-event` denies group events with `mentioned=False` by
policy (verified verbatim on `agent_62cf67080ad4` — every non-@
group message returned `allow=False` with no notice). The owner's
product intent is different: the agent has the right to hear a
group it's in, it just shouldn't reply unless addressed.

`_process_message` is reordered so classification happens FIRST,
then authorize-event runs only on paths where the agent will
actually reply / invoke tools / call the model. Specifically:

- `dm` / `group_mention` → authorize-event REQUIRED (unchanged;
  matches guide's intended domain).
- `group_silent` → authorize-event SKIPPED when the class-level
  constant `SILENT_BYPASS_AUTHORIZE = True`. The memory-only path
  proceeds regardless of Narra's decision.

`SILENT_BYPASS_AUTHORIZE` is a known-conflict-with-the-guide
constant, documented in the source and kept as a class field so it
can be flipped back to False in one line if NarraMessenger tightens
enforcement (in which case group non-@ collapses to Slack parity —
memory only when @-mentioned).

Renegotiation with the NarraMessenger team is in flight; the design
argument is that "listening to a room the agent has joined" is a
Matrix-native right, not a Narra-specific exception. When we hear
back, either the constant stays True (Narra agrees), or flips to
False and the design falls back to guide-strict.

## 2026-07-02 (post-Commit-7 hotfix) — diagnostic INFO + auto-disable broken creds

Two behavioural fixes surfaced during the first live E2E:

1. **Silent-path diagnostic logs promoted to INFO.** The
   `_process_message` decision points — client-missing drop, echo
   drop, authorize-event deny (with or without notice), classifier
   result — now log at INFO, and the silent-buffer lifecycle
   (`_enqueue_silent`, `_debounce_flush` fire/cancel, `_flush_silent`
   entry/exit, `_drain_all_silent_buffers`) is fully traced. Rationale:
   the first live test showed 0 `silent=True` runs on group non-@
   messages, and every silent-path failure mode was invisible at
   INFO. Now the log tells us exactly which branch a missing turn
   fell through.
2. **Broken credentials auto-disable on connect.** When
   `MatrixTrigger.connect` sees a `connection_mode='matrix'` row with
   an empty `matrix_access_token`, it now calls `disable_credential`
   BEFORE raising `ValueError`. Without this, the base's
   `_subscribe_loop` treated the ValueError as transient and retried
   every 120s forever, generating an ERROR + full stack trace on
   every retry. Pre-existing rows (pre-Matrix binds that never got a
   Matrix token) now flip `enabled=False` on first sight and the
   owner sees a single WARN telling them to re-bind, not a
   permanently noisy log stream.

## 2026-07-02 (Commit 7) — polling deleted; sole NarraMessenger trigger

`channel_name` reverts from `narramessenger_matrix` to `narramessenger`;
the `_matrix` suffix only existed to partition dedup / audit while the
polling `NarramessengerTrigger` coexisted. That trigger was deleted in
Commit 7, so the disambiguating suffix becomes clutter.

Practical consequence: any dedup rows written under
`channel_name='narramessenger_matrix'` become orphaned in
`channel_seen_messages`. Event-id uniqueness makes cross-partition
collisions impossible, so the orphans are just dead rows — cheap to
sweep via retention TTL, no data migration required.

`load_active_credentials()` no longer filters by `connection_mode`; it
calls the manager's `list_active()` and returns every enabled row.
Legacy Gateway rows without a `matrix_access_token` reach `connect()`,
raise `ValueError`, and get disabled by the base's watcher — the owner
is expected to re-run the bind flow to end up on Matrix.

The `_subscriber_key` still returns `matrix:<agent_id>`; provenance
documentation only, since no other trigger competes for the base's
subscriber map. Any consistent per-credential key works after
Commit 7 — no need to change it and lose the historical breadcrumb.

## 2026-07-02 (Commit 6) — auto-join invited rooms

The sync loop now walks `resp.rooms.invite` at the top of every batch
and calls `client.join(room_id)` for each invitee. Matches the setup
guide's OpenClaw config which sets `autoJoin: "always"`: the owner (or
any legitimate inviter) expects the agent to appear in the room without
a second confirmation step.

Failure handling: `client.join` errors are logged at WARNING and swallowed.
Rationale — the invite persists in `resp.rooms.invite` until the join
succeeds, so the next sync tick automatically retries. Propagating would
break the outer sync loop over a transient network blip and force a full
reconnect via the base's backoff path, which is a bigger hammer than
the failure warrants.

No dedicated audit event: auto-join is expected behaviour, not an
anomaly. If join throughput ever becomes ops-visible (e.g. mass
invites causing rate-limit trouble), promote to an audit line then.

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
