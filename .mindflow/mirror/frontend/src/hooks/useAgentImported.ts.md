---
code_file: frontend/src/hooks/useAgentImported.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — `open:false` 也可能让零自有 agent 的用户得到一个选中

只加了注释、行为来自 `configStore.refreshAgents`：查看者此前没有任何自己的 agent 时，这次
refresh 会选中第一个自己的 agent（优先引导 agent），即使 `open:false`——不导航，但空选中不再
保持为空。open:true 时随后照旧显式选中 `results[0]`。

# hooks/useAgentImported.ts — shared post-import side effect

## Why it exists

Every migration entry point (the sidebar "+" Import in [[Sidebar]], the
guided-flow offer in [[MigrationGuide]]) needs the same post-apply wiring:
refresh the agent list, select the new agent, navigate to its chat. The two call
sites had byte-identical copies that could drift — this hook is the single
definition. Reads stores via `getState()` so it depends only on `navigate`.

## Design decisions

- **Takes a LIST** (2026-08-27): [[ImportAgentModal]] imports every checked row
  in one batch, so one run can create several agents. The first result is the one
  selected — it is the topmost row the user saw, per
  [[migrationImportQueue]]'s ordering.
- **`{ open: false }` refreshes without navigating.** That's the summary's
  "Close" path: agents that just landed must appear in the sidebar regardless,
  or they stay invisible until a reload — but the user asked not to be moved.
