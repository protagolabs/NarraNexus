---
code_file: packages/narranexus-contracts/src/narranexus/contracts/prompt.py
last_verified: 2026-09-07
stub: false
---

# contracts/prompt.py — the prompt contracts

Kind `prompt`: `PromptContext` (what a section sees: agent/user, ctx_data, narratives, module instructions, db, the host runtime, deployment mode, mutable `part_sizes`/`meta`), `PromptSectionProvider` (slot `prompt.sections`, many: `id`, `order`, `budget_chars`, `render(ctx)`), `PromptAssembler` (slot `prompt.assembler`, one: `assemble(sections, ctx)`), `RenderedSection`, `budget_report()`.

## 2026-09-07 — RenderedSection.budget_chars; budget_report defaults to it

A rendered section carries its provider's declared budget; budget_report(sections, budgets=None) uses it unless a budgets mapping overrides by id. Before this the only assembler was constructed without budgets and the overrun warning was a dead branch.
