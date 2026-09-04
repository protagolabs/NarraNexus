---
code_file: src/xyz_agent_context/module/awareness_module/data_access.py
last_verified: 2026-09-04
stub: false
---

# awareness_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: update_awareness (the store resolves the instance id — a platform query — and the provider does the carry-over + upsert) and update_agent_profile (the shared rename transaction the twin route calls too). Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.
