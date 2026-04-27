---
code_file: backend/routes/dashboard.py
last_verified: 2026-04-21
stub: true
---

# dashboard.py

## 为什么存在

Dashboard v2.1 的 API 端点集合（`/api/dashboard/*`），为前端 dashboard 视图提供聚合数据。相对于 `agents.py` 里按 agent_id 查单个资源，这里的 endpoint 是面向多 agent 概览的：grid、agent cards、pending jobs queue、per-job detail drawer。

## 2026-04-21 · v2 时区协议适配

- 聚合 endpoint（`/api/dashboard/grid`）里 `pending_jobs_items` 的字段从 `next_run_time` 换成 `next_run_at` + `next_run_timezone`
- 单 job detail endpoint：SELECT 列表 + response dict 都从 α（UTC）换成 β（local + tz）
- `blocking_dependencies` placeholder 结构也同步为 β 形态

**UTC 不再出现在任何 response payload**——前端拿到字符串直接渲染，不做 Date() 转换。

## 新人易踩坑

- 任何在本文件里 `SELECT next_run_time ... FROM instance_jobs` 的查询都是错的——它绕开了协议。必须 SELECT β 列并在 response 里暴露 β
- 排序/筛选的 "时间 cursor" 如果真的要做，内部可以查 α（`next_run_time` UTC），但 response payload 永远只给 β
