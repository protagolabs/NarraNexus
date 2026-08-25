---
code_file: tests/backend/test_dashboard_status_schema.py
last_verified: 2026-08-25
stub: false
---

# test_dashboard_status_schema.py — Dashboard 状态响应契约回归

Dashboard 聚合路由会把所有可恢复的 Job 状态放入 `pending_jobs`。本测试锁定
`cooling`、`paused_no_quota`、`blocked_failed` 与本地化调度字段都能通过响应模型，
同时确保对应 QueueCounts 不会被模型静默丢弃，防止运行时已有这些状态时在
FastAPI 序列化阶段变成整页 500。
