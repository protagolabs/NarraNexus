"""
Unified Agent Memory system (refactor/agent-memory, 2026-06-03).

One record shape, one Engine of fixed lifecycle steps, one Spec per kind
(mechanism vs policy). Retrieval is vector-free: BM25 + grep + structured
filters, with the LLM as the relevance arbiter.
"""
from narranexus.platform.memory.record import (
    MemoryRecord,
    new_record_id,
    SCOPE_AGENT,
    SCOPE_USER,
    SCOPE_NARRATIVE,
    SCOPE_INSTANCE,
    SCOPE_GLOBAL,
)
from narranexus.platform.memory.spec import (
    MemoryKindSpec,
    RecallWeights,
    register_spec,
    get_spec,
    all_kinds,
    passive_kinds,
)
from narranexus.platform.memory.engine import MemoryEngine
from narranexus.platform.memory.coordinator import (
    MemoryCoordinator,
    MemoryHit,
    format_memory_hits,
)

# The memory kinds themselves are the builtin.memory_kinds plugin (plugins/,
# batch 6b): ``spec.ensure_builtin_kinds`` registers them on first lookup through
# the kernel — the platform never imports the plugin by name.

__all__ = [
    "MemoryRecord",
    "new_record_id",
    "SCOPE_AGENT",
    "SCOPE_USER",
    "SCOPE_NARRATIVE",
    "SCOPE_INSTANCE",
    "SCOPE_GLOBAL",
    "MemoryKindSpec",
    "RecallWeights",
    "register_spec",
    "ensure_builtin_kinds",
    "get_spec",
    "all_kinds",
    "passive_kinds",
    "MemoryEngine",
    "MemoryCoordinator",
    "MemoryHit",
    "format_memory_hits",
]
