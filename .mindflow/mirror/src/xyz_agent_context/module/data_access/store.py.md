---
code_file: src/xyz_agent_context/module/data_access/store.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The data-access seam MCP tools depend on instead of touching repositories/db
directly (blueprint P0). Lets the transport be swapped by the composition root
without changing any tool (rule #9/#20).

## Model

`AgentDataStore` protocol; two impls:
- `DirectStore` — direct repository access, byte-for-byte the pre-abstraction
  behaviour (local `bash run.sh` / DMG own the sqlite db). Db access goes
  through `XYZBaseModule.get_mcp_db_client` — the one loop-aware MCP entry
  point every other tool uses.
- `HttpStore` — calls the backend API forwarding the caller identity headers;
  mcp holds NO db creds (the RCE-remediation goal). Rule #21: HTTP hop, not
  import. Identity gets the call PAST auth (Q6); per-route OWNER checks are
  PR-2's work — do not claim them before they exist.

The interface grows one method per migrated tool (awareness's update is
first; a read method arrives with its first real caller — YAGNI). Both impls
MUST return the SAME strings for the same scenario, and the parity tests
compare the two implementations against one shared fake-backend semantics,
not each against a constant.

## The backend response contract (pre-review C1/C2 — the load-bearing part)

- The agents routes report failure as **HTTP 200 + `{"success": false,
  "error": ...}`** — non-2xx only ever comes from transport/middleware (e.g.
  the Q6 identity 401). An Http method must parse the body; a status-code
  check calls every failure a success.
- The PUT route's convenience default AUTO-CREATES a missing instance (the
  frontend contract). HttpStore opts out via `create_missing=false` so an
  unknown, LLM-supplied agent_id stays an ERROR exactly like DirectStore —
  without that switch the Http path would mint instances for arbitrary ids.
- HTTP-layer failures (401/5xx/unreachable/non-JSON) degrade to in-band
  `"Error: ..."` strings, never exceptions — DirectStore only ever returns
  strings, and a 401 here means the deploy flipped NARRANEXUS_BACKEND_URL
  before provisioning identity keys (ordering contract in factory.py).

## Gotchas

- DirectStore does NOT check the upsert's boolean result — faithful to the
  pre-seam tool. The Http path surfaces the backend's "Failed to update
  awareness". A known, documented asymmetry: Http is strictly more honest,
  Direct is bug-compatible; fixing Direct means changing local behaviour and
  belongs to its own change, not this seam.
