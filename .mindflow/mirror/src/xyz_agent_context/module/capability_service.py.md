---
code_file: src/xyz_agent_context/module/capability_service.py
last_verified: 2026-09-04
stub: false
---

# module/capability_service.py — per-agent capability enablement + context budget

## Intent

Which registered modules take part in an agent's turns is the owner's choice (plugin platform batch 5c). The service is the one reader (`enabled_map`, consulted by `ModuleLoader` every turn) and the one writer (`set_enabled` / `reset`, behind the owner-gated `/api/agents/{id}/capabilities` routes) over `agent_capabilities`. The no-row default is the budget rule of spec §12: a builtin module is on, a module a plugin installed is OFF for existing agents until the owner turns it on — installing a plugin must not silently grow every agent's prompt. Base modules (`ModuleConfig.base`: chat, awareness, basic info) are locked on; a turn cannot run without them.

## Design decisions

- **Owner decides builtin-ness.** `default_enabled` reads the module registry's owner (`builtin.*`), not a list — a plugin module is recognised the moment it registers.
- **Budget is declared, not measured.** `budget()` sums the modules' `context_cost_hint` (order-of-magnitude tokens each module declares) for the enabled set against the builtin baseline; `warn_if_over_budget` logs past 2× (`BUDGET_WARN_RATIO`). It is a signal for the owner and on-call, never an automatic disable (the platform does not police choices).
- **Applies next run.** The loader reads the table per turn; nothing hot-reloads a running loop.
