# NarraNexus External API — integrator contract

The protocol an external application (Arena 客服, custom chat widget,
embedded bot, etc.) speaks to a NarraNexus instance to drive a
specific agent and isolate per-visitor sessions. Authoritative
reference for everything under the `/v1/external/*` namespace.

This API is **only registered when the instance boots with
`ENABLE_EXTERNAL_API=1`**. Default deployments (cloud at
`agent.narra.nexus`, `bash run.sh` local mode, the Tauri DMG, the
Manyfold container image) leave this unset and the entire
`/v1/external/*` surface returns 404 — they keep using the original
`/api/*` endpoints and WebSocket transport.

---

## Table of contents

1. [Concepts](#1-concepts)
2. [Auth model](#2-auth-model)
3. [Error envelope](#3-error-envelope)
4. [`GET /v1/external/healthz`](#4-get-v1externalhealthz)
5. [`POST /v1/external/chat/completions`](#5-post-v1externalchatcompletions)
6. [`GET /v1/external/agents/{agent_id}/sessions`](#6-get-v1externalagentsagent_idsessions)
7. [`DELETE /v1/external/agents/{agent_id}/sessions/{session_id}`](#7-delete-v1externalagentsagent_idsessionssession_id)
8. [Session lifecycle & TTL](#8-session-lifecycle--ttl)
9. [Migration vs Manyfold](#9-migration-vs-manyfold)

---

## 1. Concepts

### Agent

A NarraNexus agent. Created and configured by an account owner via the
NarraNexus UI. From the external integrator's perspective the agent is
opaque: you address it by `agent_id` (an `agt_<hex>` string) and don't
configure its skills, memory, or providers — that's the owner's job.

### API key (`nxk_` token)

An agent-bound credential the owner mints from the "External API
Access" panel on the agent detail page. The token has a permanent
scoping to **exactly one agent_id**. Owners can:

- Mint as many tokens per agent as they want (e.g. "prod", "staging",
  "Arena", "Telegram bot")
- Rotate any token (issues a fresh secret, the old one keeps working
  for 7 days)
- Revoke any token (immediate 401 on the next request)

Tokens take the form `nxk_apk_<12 hex>_<64 url-safe chars>`. Only the
first ~12 chars (the prefix) are shown in the owner's UI after
creation — the rest is shown ONCE at create-time and never recoverable.

### Session

The unit of conversation isolation. Every chat request must carry a
`session_id` in the request metadata. NarraNexus uses it to:

- Maintain narrative memory per-session (visitor A's coffee
  preferences won't leak into visitor B's chat)
- Track per-visitor message history
- Bill all visitors' LLM tokens to the agent owner's account

You choose the `session_id` shape. Stable identifiers (cookie value,
user account id, browser fingerprint) get continuous conversations
across requests. Volatile identifiers (random UUID per page load) get
isolated one-shot sessions.

### user_type

Two values control TTL behaviour: `permanent` (typical for logged-in
users) and `guest` (typical for anonymous visitors). See §8.

---

## 2. Auth model

Every `/v1/external/*` endpoint except `/healthz` requires:

```
Authorization: Bearer nxk_apk_<key_id>_<secret>
```

The agent owner gets this string ONCE when they click "Create" or
"Rotate" in the UI. You must:

- Store it in your secrets vault (not in source, not in env vars
  exposed to client code, not in logs)
- Never echo it back to your own users
- Rotate if you suspect exposure (the UI's "Rotate" button issues a
  new token with a 7-day grace window on the old)

### Identity propagation

There is NO `X-User-Id` header for this protocol. The token IS the
identity: every request acts as the agent owner (for billing,
provider attribution, and the agent's behavioural identity), with
per-visitor isolation tracked by `metadata.session_id`.

### Per-session runtime restrictions (v0.4)

External-API turns run through a restricted variant of the agent
runtime (`ExternalAgentRuntime` with `EXTERNAL_API_POLICY`). The
restrictions are:

- **Memory is per-user scoped.** Observations distilled from one
  visitor's turn are stored at `scope_type=SCOPE_USER,
  scope_id=<visitor's user_id>` and recall filters by the same
  scope — visitor A never sees visitor B's facts even on the same
  agent. Owner-facing chat / Lark / Job paths keep using the agent
  scope they always did; this only applies inside
  `/v1/external/*`.
- **Identity is rendered as "visitor", not "owner".** The agent's
  system prompt says "you are serving an external visitor (session:
  X); the agent owner is Y" instead of the default
  owner-perspective framing — so prompt-injection attempts like
  "speak as the admin" land against an agent that already knows it's
  serving a visitor.
- **Mutating tools are not exposed.** The MCP tool
  `update_awareness` (which would let a visitor edit the agent's
  identity prompt) is suppressed. The SDK built-ins `Write`, `Edit`,
  `NotebookEdit`, and `Bash` are added to the SDK's
  `disallowed_tools` list — visitor sessions can `Read`, `Glob`,
  `Grep` to consult owner-prepared materials, but cannot mutate the
  workspace or shell out.
- **Cross-session modules are not loaded.** `SocialNetworkModule`
  (agent-wide entity graph), `LarkModule` / `SlackModule` /
  `TelegramModule` (IM channels), and `MessageBusModule` (inter-agent
  bus) are skipped — a visitor can't pollute IM bus channels or the
  agent's social entity store.

These restrictions are not configurable per token in v0.4 — they
apply to every `/v1/external/*` request uniformly. Token scopes
(`chat`, `session.delete`, `session.list`) only gate WHICH endpoints
the token can call.

### Bridged identity for logged-in users (v0.5)

By default every external API turn runs against an ephemeral user_id
derived from `metadata.session_id` (the v0.4 behaviour). For a trusted
first-party integrator (e.g. Arena's own server-side proxy) we offer
an opt-in bridge so a logged-in NetMind user gets unified memory across
this integration AND their direct chat on the main site:

1. The owner mints a token that includes the `bridge_identity` scope
   (this scope is NOT in `_DEFAULT_SCOPES` — must be explicitly
   granted).
2. The integrator passes the real NetMind userSystemCode in
   `metadata.user_id`:

   ```json
   {
     "model": "agent_xxx",
     "messages": [...],
     "metadata": {
       "session_id": "anon_or_tracking_cookie",
       "user_type": "permanent",
       "user_id": "8773c1b2..."
     }
   }
   ```
3. The route layer enforces three guards before honouring the bridge:
   - 403 `bridge_not_allowed` if the token doesn't carry
     `bridge_identity`
   - 400 `unknown_user` if the user_id isn't in the `users` table
   - 400 `not_a_real_user` if the row's `owned_by_agent` is non-NULL
     (i.e., it's some other agent's ephemeral, not a real account)
4. On all three passing, the chat skips the ephemeral mint and uses
   the real user_id directly — memory / narrative / direct-chat
   history are all under the same `user_id`.

Without `metadata.user_id` (or without the scope) the v0.4 ephemeral
path runs unchanged.

**DELETE in bridged mode is a natural no-op**: the handler computes
`ephemeral_user_id` from session_id and looks it up; the bridged
path never wrote an ephemeral row, so the cascade returns all-zero
counts. Real-user data is therefore safe from accidental wipe via
`DELETE`. List sessions also filters by `owned_by_agent` so real
users do not surface there.

### Failure codes

| Code | Meaning |
|---|---|
| 401 `missing_auth` | No `Authorization: Bearer ...` header |
| 401 `invalid_token` | Wrong prefix, malformed shape, or SHA256 mismatch |
| 401 `revoked_token` | Token was revoked by the owner |
| 401 `expired_token` | `expires_at` has passed |
| 403 `insufficient_scope` | Token lacks the scope this endpoint needs |
| 403 `agent_mismatch` | URL or `model` field points at an agent the token isn't scoped to |
| 403 `bridge_not_allowed` (v0.5) | Token lacks `bridge_identity` scope but request carried `metadata.user_id` |
| 400 `unknown_user` (v0.5) | `metadata.user_id` is not a known NarraNexus user |
| 400 `not_a_real_user` (v0.5) | `metadata.user_id` belongs to some other agent's ephemeral, rejected |
| 404 — | Endpoint doesn't exist (deployment didn't set `ENABLE_EXTERNAL_API=1`) |

---

## 3. Error envelope

`/v1/external/chat/completions` errors return the OpenAI error envelope
so OpenAI SDK clients interpret them correctly:

```json
{
    "error": {
        "message": "this token is scoped to agent 'agt_abc12345' but the request's `model` field is 'agt_other'",
        "type": "invalid_request_error",
        "code": "agent_mismatch"
    }
}
```

`/v1/external/agents/*/sessions/*` errors use the standard FastAPI
`HTTPException` shape:

```json
{ "detail": { "code": "agent_mismatch", "message": "..." } }
```

---

## 4. `GET /v1/external/healthz`

The one endpoint that does **not** require auth. Used by Kubernetes
readiness probes and third-party uptime monitors.

```
GET /v1/external/healthz
→ 200 {"status": "ok", "service": "narranexus-external-api"}
```

Always returns 200 once the FastAPI app is up. Does NOT touch the
database — a brief DB blip should not flap pod readiness.

---

## 5. `POST /v1/external/chat/completions`

OpenAI-compatible chat completions. Each call triggers a fresh agent
turn (`BackgroundRun`) and streams events back mapped to OpenAI's
chunk format.

### Request

```json
{
    "model": "<agent_id>",
    "messages": [
        {"role": "user", "content": "Hello, who are you?"}
    ],
    "stream": true,
    "metadata": {
        "session_id": "<your stable per-visitor identifier>",
        "user_type": "guest",
        "context": {
            "page_url": "https://...",
            "order_id": "..."
        }
    }
}
```

| Field | Required | Notes |
|---|---|---|
| `model` | ✓ | **Must equal the token's scoped `agent_id`**. Mismatch → 403 `agent_mismatch`. |
| `messages` | ✓ | OpenAI standard. Only the last `role:"user"` entry is fed as input — earlier turns come from NarraNexus's memory store keyed by your `session_id`. Multi-modal blocks: text parts extracted; image/audio/file silently ignored. |
| `stream` | optional | Default `false`. Both modes supported. |
| `metadata.session_id` | ✓ | Your stable per-visitor identifier. Same `session_id` = same memory pool = continued conversation. |
| `metadata.user_type` | optional | `permanent` (logged-in user; not subject to TTL cleanup) or `guest` (anonymous; subject to agent's TTL config). Default `guest`. |
| `metadata.context` | optional | Free-form JSON forwarded to the agent's trigger context. Useful for passing business identifiers (order id, page url, plan tier). |

### Required scope

`chat`. Tokens issued without this scope return 403 `insufficient_scope`.

### Streaming response

Standard OpenAI SSE: `data: {…}` newline-separated, `data: [DONE]`
terminator. The chunk shape uses four delta channels:

| Channel | Carries | OpenAI standard? |
|---|---|---|
| `delta.reasoning_content` | Agent thinking + LLM stream before the user-facing reply | OpenAI o1 / DeepSeek convention |
| `delta.tool_calls[]` | Internal tools the agent invokes (web search, skill execution, etc.) | Standard OpenAI |
| `delta.content` | The user-visible reply text the agent emits via `send_message_to_user_directly` | Standard OpenAI |
| `delta.tool_results[]` | Tool outputs paired by `tool_call_id` with the matching `tool_calls[]` entry | **Non-standard extension** — see below |

The `delta.tool_results[]` extension inlines tool execution outputs in
the same assistant turn (vanilla OpenAI would expect the client to
execute and re-post; NarraNexus has them already). Clients that don't
recognise the field can ignore it — tool calls just stay marked
"in progress" forever in the client's UI, but the agent's reply still
arrives via `delta.content`.

### `finish_reason`

- `"stop"` — agent produced user-visible text via
  `send_message_to_user_directly` (the common case).
- `"tool_calls"` — agent ran tools without producing user-visible text.

### Non-streaming response

When `stream: false`, NarraNexus accumulates the same events and
returns one synchronous `chat.completion`:

```json
{
    "id": "chatcmpl-…",
    "object": "chat.completion",
    "created": 1779800000,
    "model": "<agent_id>",
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": "<combined user-visible reply>",
            "reasoning_content": "<combined thinking + token stream>",
            "tool_calls": [...],
            "tool_results": [...]
        },
        "finish_reason": "stop"
    }]
}
```

### curl example

```bash
NXK_TOKEN="nxk_apk_a1b2c3d4_xxxxxxxxxxxx"
AGENT_ID="agt_abc12345"

curl -N -X POST "https://your-narranexus-host/v1/external/chat/completions" \
  -H "Authorization: Bearer $NXK_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$AGENT_ID\",
    \"messages\": [{\"role\": \"user\", \"content\": \"hello\"}],
    \"stream\": true,
    \"metadata\": {
      \"session_id\": \"visitor_42\",
      \"user_type\": \"guest\"
    }
  }"
```

### Errors

| Status | Code | Cause |
|---|---|---|
| 400 | `no_user_message` | `messages` empty or no `role:"user"` |
| 400 | `invalid_request` | metadata.session_id missing or malformed |
| 401 | (per §2) | auth-related |
| 403 | `agent_mismatch` | `model` field doesn't match token's agent |
| 403 | `insufficient_scope` | token lacks `chat` scope |
| 500 | `api_error` | mid-stream fatal error |

---

## 6. `GET /v1/external/agents/{agent_id}/sessions`

List every ephemeral session the integrator has touched on this agent.
Useful for auditing "which of my visitors' sessions are still alive in
NarraNexus" against your own session-id store.

### Query parameters

| Param | Default | Notes |
|---|---|---|
| `limit` | 100 | 1–500 range |

### Required scope

`session.list`.

### Response

```json
{
    "object": "list",
    "agent_id": "agt_abc12345",
    "data": [
        {
            "session_id": "visitor_42",
            "user_id": "ext_bc12345_visitor_42",
            "user_type": "external_guest",
            "message_count": 8,
            "agent_narrative_total": 12,
            "created_at": "2026-06-10T14:00:00+00:00",
            "last_message_at": "2026-06-11T09:23:11+00:00"
        }
    ],
    "count": 1
}
```

Sessions are returned newest-activity-first. `user_id` is NarraNexus's
internal identifier; integrators don't normally need it (handy for
debugging cross-referencing logs).

### Caveats

- `agent_narrative_total` is the agent-wide count, not per-session
  (cheap proxy; cross-session narratives are rare and the cost of an
  exact count outweighs the benefit).
- `session_id` may not exactly match what you sent if your original
  contained characters outside `[a-zA-Z0-9_-]` — NarraNexus sanitises
  on storage. The integrator's own session-id store remains
  authoritative.

---

## 7. `DELETE /v1/external/agents/{agent_id}/sessions/{session_id}`

Cascade-delete every trace of a session: the `users` row, every row
keyed by `user_id` across 13 tables (narratives, agent_messages,
module_instances, instance_jobs, …), and the per-(agent, user)
workspace directory on disk.

### Required scope

`session.delete`.

### Response

```json
{
    "deleted": true,
    "session_id": "visitor_42",
    "user_id": "ext_bc12345_visitor_42",
    "cascade": {
        "events": 12,
        "module_instances": 5,
        "user_providers": 0,
        "users": 1,
        "workspace_dirs_removed": 2,
        "workspace_bytes_removed": 142336,
        ...
    }
}
```

The `cascade` map shows rows removed per table. Useful for audit logs.

### Idempotency

Calling DELETE on a non-existent session returns 200 with all-zero
counts — not a 404. Treat this as success. Your retry logic should
expect a no-op response on the second call.

### When to call

- When your visitor signs out / logs out
- When your conversation timeout expires
- When the visitor explicitly requests data deletion (GDPR right-to-
  erasure)

You should NOT wait for the integrator to call this — NarraNexus
provides a TTL safety net (§8) precisely because calls get lost.

### Errors

| Status | Code | Cause |
|---|---|---|
| 403 | `agent_mismatch` | URL agent_id ≠ token's agent |
| 403 | `insufficient_scope` | token lacks `session.delete` scope |

---

## 8. Session lifecycle & TTL

```
                  Session created
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   user_type=permanent         user_type=guest
   (logged-in user)            (anonymous visitor)
        │                             │
        │ Not eligible for            │ Eligible for TTL cleanup
        │ TTL cleanup                 │ (if owner configured a TTL
        │ (still cleanable            │  on the agent)
        │  by explicit DELETE)        │
        │                             │
        ▼                             ▼
   Lasts until                 Lasts until either:
   explicit DELETE             - explicit DELETE, OR
                               - TTL elapses since last activity
```

### How TTL works

The agent owner configures `external_session_ttl_seconds` on the agent.
A background poller scans every ~5 minutes for guest users whose last
agent_message is older than the TTL, and runs the same cascade-DELETE
that §7's endpoint runs.

- TTL is **opt-in**. NULL TTL means the owner doesn't want auto-cleanup;
  no surprise deletion ever happens.
- There's **no system-side minimum** TTL. If the owner sets TTL=60s,
  the poller honours it.
- Permanent users (`user_type=permanent`) are NOT subject to TTL
  regardless. They lasts until explicit DELETE.

### Implications for integrators

- Set `user_type` correctly per visitor (logged-in → `permanent`,
  anonymous → `guest`).
- The default is `guest` if you omit `user_type`. Deliberate: forgetting
  the field is safer to err toward "cleanable" than "accumulating
  forever."
- Always call DELETE when you know a session is over. TTL is a safety
  net, not a primary cleanup mechanism — relying on it means
  conversations time out at the owner's threshold instead of yours.

---

## 9. Migration vs Manyfold

This protocol is the **sibling** of the Manyfold protocol
(`docs/manyfold-api.md`), not its replacement. Choose:

| Question | Manyfold | This protocol |
|---|---|---|
| Who deploys the container? | Manyfold platform team | Self-hosted by the agent owner |
| Tenancy | Single tenant (one container, one Manyfold runtime owner) | Single tenant too, but with per-session user isolation |
| Auth secret | Container-wide gateway token | Per-agent API key |
| Identity per call | `X-User-Id` header (with `mf_<id>` shape) | `metadata.session_id` (your shape) |
| Path | `/v1/chat/completions` | `/v1/external/chat/completions` |
| Use this for | Manyfold platform integration | Any custom external app: chat widgets, embedded bots, batch pipelines |

You can have BOTH enabled on the same NarraNexus instance —
`/v1/chat/completions` (Manyfold) and `/v1/external/chat/completions`
(this protocol) are physically separate routes with separate middlewares.
nxk_ tokens never satisfy a Manyfold check and vice versa.

---

## Appendix: OpenAI SDK integration

The protocol is OpenAI-compatible enough that the official OpenAI SDK
client works with just a base_url + token swap:

```python
from openai import OpenAI

client = OpenAI(
    api_key="nxk_apk_...",
    base_url="https://your-narranexus-host/v1/external",
)

stream = client.chat.completions.create(
    model="agt_abc12345",
    messages=[{"role": "user", "content": "hello"}],
    stream=True,
    extra_body={
        "metadata": {
            "session_id": "visitor_42",
            "user_type": "guest",
        }
    },
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

`extra_body` is the OpenAI SDK's escape hatch for non-standard fields;
NarraNexus reads `metadata` from there.
