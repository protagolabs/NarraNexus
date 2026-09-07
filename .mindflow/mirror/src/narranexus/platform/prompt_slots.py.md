---
code_file: src/narranexus/platform/prompt_slots.py
last_verified: 2026-09-07
stub: false
---

# platform/prompt_slots.py — the prompt slot seam

`ensure_registered` (lazy manifest registration of builtin.prompts), `sections_for(registries)` (bound order when bound, else declared `order`), `assembler_for(registries)` (the bound `prompt.assembler`). ContextRuntime builds the system prompt only through these.
