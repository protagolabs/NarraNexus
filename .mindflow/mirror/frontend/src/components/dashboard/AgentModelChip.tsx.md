---
code_file: frontend/src/components/dashboard/AgentModelChip.tsx
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — 折叠行的模型 chip（纯展示）

Dashboard 折叠行名字列下的一行小 chip：显示 agent 槽 effective 模型 +
inherit(default)/override(custom) 标记。**纯组件**，`entry` 来自
[[DashboardPage]] 的单次 `getAgentsModelOverview()` 批量拉取（不自己发请求，
避免 per-agent N+1）；无 entry 则渲染 null。抽成独立组件是为了可单测（页面
本身依赖过重不宜整体渲染测试）。
