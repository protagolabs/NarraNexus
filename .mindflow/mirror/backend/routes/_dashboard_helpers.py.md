---
code_file: backend/routes/_dashboard_helpers.py
last_verified: 2026-04-21
stub: true
---

# _dashboard_helpers.py

## 为什么存在

Dashboard v2.1 视图里多 agent 聚合查询的内部 helper。职责包括：拉 live jobs per agent by state（`_fetch_live_jobs_by_state`）、拉 recent events per agent、拼各种聚合（metrics_today、attention banners、health）。这些查询都是多 agent 批量、有去重/总序语义，不适合放在 route handler 里以免每个 endpoint 各自重写。

## 2026-04-21 · v2 时区协议适配

- `_fetch_live_jobs_by_state()` 的 SELECT 列表从 `next_run_time` / `last_run_time`（UTC）换成 β 列 `next_run_at_local` + `next_run_tz` + `last_run_at_local` + `last_run_tz`
- 返回 dict 的 key 也换成 `next_run_at` / `next_run_timezone` / `last_run_at` / `last_run_timezone`
- 调用方（`dashboard.py`）读这些新 key 拼最终 API response

## 新人易踩坑

- 此文件只出 β 数据，**不要**再从这里读 `next_run_time`/`last_run_time`——它们是 poller 内部字段
- 跨 agent 聚合的"下次运行排序"应该**避免**在这一层做（β 是用户本地文本，不同 tz 间无可比性）。如果确实要做时间排序，必须改用 α 列 + 仅在 API 层转换回 β 展示
