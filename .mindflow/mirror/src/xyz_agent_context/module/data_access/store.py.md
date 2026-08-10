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
  behaviour (local `bash run.sh` / DMG own the sqlite db).
- `HttpStore` — calls the backend API forwarding the caller identity headers;
  mcp holds NO db creds (the RCE-remediation goal). Rule #21: HTTP hop, not import.

The interface grows one method-pair per migrated module. Awareness is first
(`get_awareness` / `update_awareness`). Both impls MUST return the SAME strings
(`_AWARENESS_OK` / `_no_instance_msg`) so migration is parity-preserving.

## Gotchas

- HttpStore maps a backend 404 to the same "no instance" message DirectStore
  returns, so callers can't tell the transports apart.
