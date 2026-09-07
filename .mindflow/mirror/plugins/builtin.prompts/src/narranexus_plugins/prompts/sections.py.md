---
code_file: plugins/builtin.prompts/src/narranexus_plugins/prompts/sections.py
last_verified: 2026-09-07
stub: false
---

# builtin.prompts — sections.py

Security (cloud only, order 10), Temporal (skipped under turn-context relocation, 20), Narrative (main narrative via NarrativeService, records `nar_*` meta, 30), Modules (runtime's module-instruction formatter, 40), Bootstrap (first-run injection, deletes Bootstrap.md past threshold, sets `ctx_data.bootstrap_active`, 50). Each renders through the `PromptContext.runtime` helpers; `CONTRIBUTIONS` is the manifest's tuple.
