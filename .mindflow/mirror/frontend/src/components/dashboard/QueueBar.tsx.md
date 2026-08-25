---
code_file: frontend/src/components/dashboard/QueueBar.tsx
last_verified: 2026-08-25
stub: false
---

# QueueBar.tsx — Agent 队列状态的紧凑分布条

## 为什么存在

Agent 卡片需要在很小的空间里同时表达队列总量和不同状态的占比。该组件只负责这份状态摘要，不承担 Job 操作或状态推导。

## 上下游关系

- **调用方**：Dashboard 的 Agent 卡片。
- **契约来源**：[[../../types/api.ts]] 的 `QueueCounts`；后端新增可见队列状态时，颜色、顺序和本地化标签必须同步覆盖。

## 设计决策

- 恢复/等待类状态共用 warning 色，依赖失败和执行失败共用 error 色。
- resilience 状态复用 Jobs 面板已有翻译，避免维护两套不同命名的状态文案。

## Gotcha

状态颜色表使用穷尽类型约束。`QueueCounts` 新增字段会让类型检查失败，这是刻意保留的同步保险。
