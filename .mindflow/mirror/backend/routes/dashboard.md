---
code_file: backend/routes/dashboard.py
last_verified: 2026-04-13
stub: false
---

# backend/routes/dashboard.py — Intent

## 为什么存在
`GET /api/dashboard/agents-status` 端点 + v2.1/v2.1.1/v2.1.2 新加的一组懒加载详情端点和 job mutation 端点。Dashboard 前端所有 HTTP 调用的唯一后端入口。

v1 已推倒重写（归档在 `.mindflow/state/archive/2026-04-13-dashboard-v1/`）。

## 端点清单（v2.1.2）
- `GET  /api/dashboard/agents-status` — 主聚合视图（polling 目标）
- `GET  /api/dashboard/agents/{id}/sparkline?hours=24` — 24h events/hour 桶（懒加载）
- `GET  /api/dashboard/jobs/{job_id}` — 单 job 全量详情
- `GET  /api/dashboard/sessions/{session_id}?agent_id=X` — 单 session 详情
- `POST /api/dashboard/jobs/{id}/retry` — failed/blocked/cancelled → pending
- `POST /api/dashboard/jobs/{id}/pause` — active/pending → paused
- `POST /api/dashboard/jobs/{id}/resume` — paused → pending

## 上下游
- **上游**：前端 `lib/api.ts` 的 7 个方法一一对应
- **下游**：
  - `_dashboard_helpers.py` — 组装、查询、派生逻辑
  - `_dashboard_schema.py` — Pydantic 响应类型（discriminated union）
  - `_rate_limiter.py` — per-viewer 2 req/s 滑窗
  - `backend/state/active_sessions.py::get_session_registry` — WS session snapshot
  - `backend/auth.py::_is_cloud_mode` / `get_local_user_id` — viewer 身份识别

## 设计决策
1. **viewer_id 永远从 session 读**（TDR-12）：cloud 走 JWT (request.state.user_id)；local 走 `get_local_user_id()`。**拒绝 `?user_id=X`** 返 400——这是 security rev-1 C-1 的修复点。
2. **`asyncio.gather(return_exceptions=False)`**（TDR-8）：4 个聚合查询（last_activity / jobs / instances / enhanced）+ v2.1 加的 2 个（recent_events / metrics_today）并发。任一失败整个 request 500——不做 partial degradation（不完整的 dashboard 比错误更误导）。
3. **Pydantic discriminated union 序列化**：`AgentStatus = Annotated[Union[Owned, Public], Field(discriminator='owned_by_viewer')]`。Literal[True/False] 让 FastAPI + OpenAPI 生成正确。
4. **Rate limit 在 endpoint 开头**：拒绝到了 DB 层之前——避免恶意流量打穿 DB 池。
5. **`HTTPException(429, headers={"Retry-After": "1"})`**：必须走 exception 的 headers 参数，直接改 response.headers 在 FastAPI 里会被 exception 路径丢掉。
6. **Lazy 端点各自 re-check ownership**：`_assert_agent_visible()` + owner-only check。Public 非自有用户**不能**读 job/session 内部。这是 security rev-1 C-2 的延伸——不仅主端点有类型层保护，每个 drill-down 端点也要检权。

## v2.1.1 bug 修复点
`_derive_kind` 和 `_earliest_started_at` 保留但 `pending_jobs_items` 构造改了——原来遍历 `per_state["pending"]` 时它是 union（pending+active+blocked+paused），导致后续遍历 "active"/"blocked"/"paused" 双重计算。v2.1.1 `fetch_jobs` 返 RAW per-state 后，本文件加 `seen_job_ids: set` 二保险去重。

## v2.2 G3 stale instance bucketing
`fetch_instances` 现在返回 `{agent_id: {"active": [...], "stale": [...]}}`。路由层：
- `instances = inst_buckets["active"]` 传给 `running_count` 和 `_derive_kind`——zombie instances 不再撑住 running 状态。
- `stale_instances_raw = inst_buckets["stale"]` 放入 `raw` dict，最终由 `to_response` 写进 `OwnedAgentStatus.stale_instances`。
- Public 变体不含 `stale_instances`（owner-only 字段）。

## Gotcha
- **query param 白名单**：目前只 check `user_id`，其他 unknown params 不拒绝。如果未来有敏感 query 要加，显式 reject。
- **`_iso()` 对 datetime 转 ISO**——但不加时区标识。MySQL 返回 naive datetime，ISO 字符串里没 `+00:00`。前端 `new Date(iso)` 会按**浏览器本地时区**解读，和后端本地时区可能不一致。长期：改后端存 timezone-aware 并输出 `+00:00`。
- **Lazy 端点的 viewer 识别**走同一 `_resolve_viewer`——保证主端点和 drill-down 语义一致。如果主端点支持某种新身份（如 API key），lazy 端点**不会自动**继承，要显式改。
- **Mutation 端点**（retry/pause/resume）目前直接改 `instance_jobs.status`——绕过了 job_trigger 的调度器逻辑。如果 job_trigger 未来加了"状态变更钩子"，这里要 audit 是否漏触发。
- `update_time` column：mutation SQL 用 `datetime('now')` 是 SQLite 方言；MySQL 需要 `NOW()`。AsyncDatabaseClient 的 `_mysql_to_sqlite_sql` 做反向翻译，原始 SQL 写 `datetime('now')` 可能在 MySQL 上出问题——**需要验证**。更稳的是 Python 侧生成 ISO 时间传参。
