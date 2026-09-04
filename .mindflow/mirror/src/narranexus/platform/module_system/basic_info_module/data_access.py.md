---
code_file: src/narranexus/platform/module_system/basic_info_module/data_access.py
last_verified: 2026-09-04
stub: false
---

# basic_info_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: view_narrative / view_event / switch_narrative over the shared `_narrative_reads` helpers the narrative twin route calls too. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.
