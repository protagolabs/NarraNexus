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
- Verified identity travels per-request in a ContextVar (`verified_caller()`),
  reset in `finally` — the policy layer reads it without re-verifying.
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
