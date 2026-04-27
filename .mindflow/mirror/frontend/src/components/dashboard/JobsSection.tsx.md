---
code_file: frontend/src/components/dashboard/JobsSection.tsx
last_verified: 2026-04-21
stub: true
---

# JobsSection.tsx

## 为什么存在

Dashboard 页面上的 "Jobs" section 组件：展示 agent 的 pending + running + failed job 队列及展开详情。和 `JobsPanel.tsx` 区别：JobsPanel 是单 agent 大视图、带 DAG 依赖图；JobsSection 是 dashboard grid 里的小卡片视图，只显示列表和一行 "next run at X (tz)" 预告。

## 2026-04-21 · v2 时区协议渲染

`next_run_time` / `last_run_time`（UTC）已从 Job 类型中删除。本组件渲染改为：

```tsx
`next ${j.next_run_at}${j.next_run_timezone ? ` (${j.next_run_timezone})` : ''}`
```

—— **直接拼接字符串，不走 `new Date()` / `formatTime()`**。因为 β 已经是"用户意义的那个时间"，任何浏览器侧的再转换都会破坏契约。展开详情里的 `Next run:` 同理。

## 新人易踩坑

- 如果看到 `job.next_run_time`、`job.last_run_time` 的字段访问——类型会报错，别硬加回来；那是协议违规
- 需要 relative time（"2 hours ago"）时，**不可以**把 β 扔进 `formatRelativeTime()`（它内部 new Date 会按浏览器 tz 解读）。要么在后端提供相对时间字段，要么继续用 β 的 naive 字符串直接展示
