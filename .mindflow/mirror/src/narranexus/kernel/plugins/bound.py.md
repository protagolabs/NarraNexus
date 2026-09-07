---
code_file: src/narranexus/kernel/plugins/bound.py
last_verified: 2026-09-07
stub: false
---

# kernel/plugins/bound.py — reading bindings at runtime

`bound_provider/bound_layer` (binding or slot default), `bound_entry` (one-arity: the bound plugin's contribution by owner or name; `UnknownEntry` when it registered nothing — loud, never a silent fallback), `bound_entries` (many-arity: filtered and ordered by the binding, else registration order). The seam every consumer of `Registries.bindings` goes through.
