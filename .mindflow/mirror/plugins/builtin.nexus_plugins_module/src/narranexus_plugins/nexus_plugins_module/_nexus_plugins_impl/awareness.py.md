---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/awareness.py
last_verified: 2026-09-07
stub: false
---

# nexus_plugins_module — awareness.py

The agent's self-awareness: `platform_overview()` (host version, mode, distribution, builtin + user plugins, slot domains, non-default bindings), `platform_slots(domain)` (the catalog), `contract_docs(kind)` (contract classes with docstrings/members + slots), `agent_self(db, agent, user)` (capabilities, model slots, prompt sections in effect), `capability_set(...)` (owner-only, refuses base capabilities, persists via CapabilityService).
