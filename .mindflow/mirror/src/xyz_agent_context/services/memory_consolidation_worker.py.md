---
code_file: src/xyz_agent_context/services/memory_consolidation_worker.py
last_verified: 2026-06-03
stub: false
---

# memory_consolidation_worker.py — unified Agent Memory

Background dirty-scope consolidation worker (§7.4): count/idle/cap triggers, processing→dirty/failed state machine, per-scope failure isolation, flush_scope. Never caps the agent loop (iron rule #14).

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
