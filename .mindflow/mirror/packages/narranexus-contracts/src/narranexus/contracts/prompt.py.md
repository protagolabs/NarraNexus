---
code_file: packages/narranexus-contracts/src/narranexus/contracts/prompt.py
last_verified: 2026-09-07
stub: false
---

# contracts/prompt.py — the prompt contracts

Kind `prompt`: `PromptContext` (what a section sees: agent/user, ctx_data, narratives, module instructions, db, the host runtime, deployment mode, mutable `part_sizes`/`meta`), `PromptSectionProvider` (slot `prompt.sections`, many: `id`, `order`, `budget_chars`, `render(ctx)`), `PromptAssembler` (slot `prompt.assembler`, one: `assemble(sections, ctx)`), `RenderedSection`, `budget_report()`.
