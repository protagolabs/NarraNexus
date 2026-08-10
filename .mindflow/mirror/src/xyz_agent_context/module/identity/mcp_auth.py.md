---
code_file: src/xyz_agent_context/module/identity/mcp_auth.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The enforcement half of MCP caller auth (blueprint P1). `_mcp_identity.py`
reads the self-declared identity; `identity/tokens.py` proves it; this module
decides what happens: the ASGI middleware every module MCP server wears
(installed once in `module_runner._build_mcp_server` — both transports, one
choke point) plus `check_agent_ownership`, the OwnerScopedPolicy the tool
wrapper calls.

## The mode contract (`NX_MCP_AUTH_MODE`)

- **off** (default) — full no-op. This is what keeps a local `bash run.sh`
  byte-identical (iron rule #7); local enforcement is a deployment choice.
- **audit** — verify + log + record denials, never reject. The measurement
  phase: its logs answer "which callers still arrive tokenless?" before
  enforce can be flipped (item3 灰度 discipline).
- **enforce** — tokenless/invalid POSTs are 401'd at the door; cross-owner
  tool calls get an in-band error value. GETs (SSE handshake) and
  /health(z) stay open — tool calls are POSTs on both transports.
- Unknown mode value reads as **audit**, not off: a typo must surface in
  logs, not silently disable auth.

## Design decisions

- **Missing public key ⇒ whole-middleware fail-open (loud warning), checked
  BEFORE any token.** With no key provisioned the broker cannot have signed
  tokens either, so every request is tokenless and enforce would take the
  entire data plane down over a deploy misconfiguration.
- **Pure ASGI, not BaseHTTPMiddleware** — composes with the streamable-HTTP
  lifespan and never re-buffers SSE streams.
- **bearer.user_id must equal token sub** — a mismatch is a forged field and
  invalidates the whole record (not treated as unknown).
- **The policy's proof is PER-MESSAGE, not the middleware ContextVar**
  (`verified_caller_for_tool_call`, PR #260 review #2, verified against mcp
  1.24 sources): on SSE the tool runs inside the GET /sse task, on stateful
  streamable HTTP inside the initialize-time session task — the ContextVar is
  a connection-time snapshot, while the self-declared facts are read
  per-message via `request_ctx`. Proof now comes from the same ambient
  headers (`_ambient_headers` + [[verify]]), and its verdict is FINAL even
  when it is "no proof" (falling back to the snapshot would resurrect the
  mismatch). ContextVar remains only the no-ambient-request fallback (direct
  calls/tests). Pinned by a REAL-transport integration test
  (`test_real_streamable_transport_carries_proof_to_the_tool` — TestClient
  driving `_build_mcp_server`'s actual app end-to-end).
- Header verification itself is the shared [[verify]] algorithm; this module
  keeps only the mode gate, the door check and the fail-open choice.
- `resolve_owner` goes through a 60s-TTL in-process cache
  (`_resolve_owner_cached`) so the hot tool-call path doesn't add one MySQL
  point-read per call; short TTL keeps it self-correcting, not a second
  source of truth.
- `check_agent_ownership` only ever TIGHTENS a proven identity: no identity /
  local mode / unknown agent (`resolve_owner` → "") all allow, so the
  fail-open baseline of `_mcp_identity` survives intact. Denials write
  `mcp_auth_denied` to instance_executor_audit in audit AND enforce mode.

## 上下游

- 安装方: [[module_runner]] `_build_mcp_server`（wrapped Starlette 的
  middleware 参数）
- 策略调用方: [[_mcp_identity]] `_wrap_fn`（async 工具，resolved agent_id）
- 签发方: 云 = deploy 仓 broker `ensure()`；本地 = [[identity/tokens]]
  `LocalEphemeralIssuer`（step_3 dispatch 时 stamp）
