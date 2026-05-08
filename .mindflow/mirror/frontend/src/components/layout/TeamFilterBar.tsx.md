---
code_file: frontend/src/components/layout/TeamFilterBar.tsx
last_verified: 2026-05-08
stub: false
---

# TeamFilterBar.tsx — Sidebar 顶部 team chips (subproject 1)

显示 `[All] [<team chips>] [Untagged]` 三类切换 + 齿轮按钮打开 `TeamManagementModal`。

`Sidebar.tsx` 包了一层 `TeamFilterAndAgents` helper，根据 chip 选择计算 `filterAgentIds` 传给 `AgentList`，让现有 AgentList 渲染保持不变。

> Sidebar collapsed 时本组件不渲染（B9 决策：v1 简化）。
