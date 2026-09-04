---
code_file: src/xyz_agent_context/module/social_network_module/data_access.py
last_verified: 2026-09-04
stub: false
---

# social_network_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: the six entity/search/stats bodies plus create_agent; `_resolve` is the old `DirectStore._social_module` (instance lookup + temp module), returning the seam's `message`-shaped failure dicts. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.
