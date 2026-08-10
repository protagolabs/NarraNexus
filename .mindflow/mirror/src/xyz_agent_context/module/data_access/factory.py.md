---
code_file: src/xyz_agent_context/module/data_access/factory.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

Composition root for AgentDataStore. Picks transport by `NARRANEXUS_BACKEND_URL`
the same way `db_factory` picks a db backend and `broker_client` gates on
`BROKER_URL`: unset → DirectStore (current behaviour, no-op change), set →
HttpStore. One env var = no scattered `if is_cloud` in tools (rule #9/#20).

`current_identity_headers()` forwards the live MCP request's identity headers
(X-NarraNexus-* / borrowed bearer, read from `_mcp_identity._ambient_headers`)
to the backend on the Http path; empty with no ambient request. Until P2 flips
cloud over by SETTING the env var, every caller gets DirectStore — so landing
this is behaviour-preserving everywhere.
