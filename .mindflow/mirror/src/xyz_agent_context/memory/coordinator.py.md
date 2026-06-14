---
code_file: src/xyz_agent_context/memory/coordinator.py
last_verified: 2026-06-14
stub: false
---

## 2026-06-14 — scope_type / scope_id kwargs added to remember() + grep_memory()

Both methods now accept optional `scope_type` and `scope_id` kwargs and
pass them through to `engine.recall()` / `engine.grep()`. Defaults
preserve today's "no scope filter" behaviour (recall ALL records for
agent_id). The v0.4 external-API path uses these to confine per-turn
auto-recall to the calling visitor's session (scope_type='user',
scope_id=visitor_id) so visitor A's facts never reach visitor B's
prompt. See [[../../module/general_memory_module/general_memory_module]]
`_user_scope_kwargs()` + `_retain_scope()`.

# coordinator.py — unified Agent Memory

MemoryCoordinator facade — cross-kind remember() (RRF-fused ranked recall) and grep_memory() (exact/regex). The '回忆' abstraction behind the agent tools.

Part of the unified memory system (`refactor/agent-memory`). Full design,
data model, retrieval stack and migration: reference/self_notebook/specs/2026-06-03-agent-memory-unification-design.md. Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
