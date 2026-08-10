---
code_file: backend/routes/jobs.py
last_verified: 2026-08-10
stub: false
---
## 2026-08-10 (PR-8) — 两个 search 路由收口到共享实现

`/search/semantic` 与 `/search/keywords` 不再自己抄 status 校验/`job_to_llm_dict`/截断，
改调 [[_job_reads]] 的 `search_jobs_semantic`/`search_jobs_by_keywords`（与 seam 的 DirectStore
及 [[agents/jobs]] 孪生路由同一实现，消除第三份手抄 drift，铁律 #8）。user_id 仍取自
`resolve_current_user_id`（严格 per-user，与 agent 面 seam 路由的宽松 user_id 相反——见
[[agents/jobs]]）。import 用 alias（`_shared_search_*`）避与同名路由函数冲突。


## 2026-08-10 (pre-open review #4) — job_type 提前解析,修 type-switch 僵尸 job

job_update 的 `effective_type` 原来在 trigger_config 分支里 `updates.get(
"job_type", job.job_type)` 读——但 job_type 分支跑在 trigger_config **之后**,
永远拿旧类型。one_off→scheduled + 新 cron 一起提交时,compute_next_run 用
one_off 算出 None,next_run_time 被写空,job 变成 status 正常却永不被调度的
僵尸。改为进 updates 前先解析 `effective_type`;**MCP 工具 _job_mcp_tools.py
同处 lockstep 一起改**(两边对称的同一 bug)。回归测试
test_update_job_type_switch_recomputes_next_run_with_new_type 钉住。


## 2026-08-10 (pre-open review) — search 收归调用者身份 + 中立错误文案

- 两个 search 端点删掉 "optional user_id" query 参数,强制
  `resolve_current_user_id`——与同文件 list_jobs 早已写明的决定一致
  (可选 user_id 让任何客户端翻别人 job,且 job_to_llm_dict 连 payload
  一起返回);一文件两套答案是漂移。
- update/pause 的 "does not belong to agent" 与 "not found" 合并为同一
  中立文案,堵跨租户 job_id 存在性预言机(narrative 路由同法)。
- 失败键:update/pause 沿 jobs 家族 response_model 用 `message`,search
  用 `error`——per-family 约定,HttpStore 端按端点 response model 解析
  (awareness 先例),不设全局单一 shape。
- keywords ≤20 个、query ≤512、limit 1..100 上界补齐。

## 2026-08-10 — MCP data-access seam: update/pause/search-semantic/search-keywords (PR-2)

Added four endpoints that are the backend half of the MCP data-access seam —
each one mirrors a specific tool in
`src/xyz_agent_context/module/job_module/_job_mcp_tools.py` exactly (same
`JobRepository` / `JobInstanceService` calls, same response shape), so a
non-agent HTTP caller gets identical semantics to what the agent's own tools
would produce:

- `PUT /{job_id}` ↔ `job_update` (L410) — partial-update semantics, same
  branch-by-branch validation (trigger_config via `TriggerConfig`, job_type
  via `JobType`, status via `JobStatus`, `next_run_time` atomic alpha/beta
  override) and the same `JobInstanceService.update_job()` delegation for
  the actual write.
- `PUT /{job_id}/pause` ↔ `job_pause` (L553) — **unconditional** pause
  (`JobRepository.pause_job` → `update_job_fields(status=PAUSED)`, no
  precondition on current status). See "Gotcha" below — this is
  deliberately a DIFFERENT code path from the dashboard's pause endpoint.
- `GET /search/semantic` ↔ `job_retrieval_semantic` (L193) — despite the
  name this is BM25 keyword ranking (`JobRepository.search_keyword`), not
  vector cosine; vectors were retired from job search project-wide.
- `GET /search/keywords` ↔ `job_retrieval_by_keywords` (L339) —
  `JobRepository.search_by_keywords`, description truncated to 200 chars +
  `"..."` in the response, same as the tool.

All four call `assert_owned(request, agent_id)` (from
`backend/routes/_ownership.py`) BEFORE entering the try/except block —
same placement as `home_assistant.py` — so ownership denial surfaces as a
real HTTPException (404 unknown / 403 not-owner / 503 lookup-failure)
instead of being swallowed into a `{"success": False, ...}` 200 payload by
the generic exception handler. Business-logic failures below that point
(job not found, wrong agent, invalid field, service failure) keep the
tool's own `{"success": False, "message"/"error": ...}` shape — those are
data-integrity responses, not authorization responses, and mixing the two
failure vocabularies would make client-side handling ambiguous.

**Gotcha — deliberate duplication with the dashboard's pause endpoint**:
`backend/routes/dashboard/routes.py` already has
`POST /api/dashboard/jobs/{id}/pause`, which goes through
`job_recovery.pause_job` (portable core, only allows pausing from
active/pending, keeps the JobTrigger state machine invariants). The new
`PUT /api/jobs/{id}/pause` here is NOT that — it replicates the MCP tool's
raw, unconditional pause instead. This looks like exactly the anti-pattern
the 2026-06-01 entry below warns about (an earlier unauthenticated pause
attempt on this file was removed for that reason), but it is intentional
this time: (1) it is gated by `assert_owned`, closing the auth gap the
2026-06-01 removal was about; (2) its purpose is different — giving a
non-agent caller (e.g. a frontend panel wanting to call "what the agent's
job_pause tool would do") the exact same semantics as the tool, not
replacing the dashboard's human-facing pause-with-precondition UX. If a
caller needs the state-machine-safe version, it must call the dashboard
route instead — the two are NOT interchangeable and will diverge on a job
that isn't currently active/pending.

## 2026-08-04 — /complex 批量创建带 confirm_new=True（W1）

/complex 的标题由调用方设计，同组子任务标题天然相近（"X part 1/2"）。
相似判重门是防 LLM 重复创建的，对确定性批量只会误伤：旧行为下第二个
子任务会被静默合并到第一个上，依赖图跟着指错。现在显式跳过相似门
（精确同名幂等仍生效）。

## 2026-06-01 — enum-derived status filter (batch ③)

The `GET /` status filter now derives `valid_statuses` from the JobStatus enum,
so the new states (paused_no_quota / cooling / blocked / blocked_failed / paused)
are accepted automatically. HISTORICAL NOTE (superseded 2026-08-10): this entry
used to say pause/resume must live only on the dashboard route because an
earlier *unauthenticated* pause attempt here was removed for the auth gap.
That gap is now closed — `PUT /{job_id}/pause` exists here again, gated by
`assert_owned`, mirroring the `job_pause` MCP tool's semantics (unconditional,
no status precondition) rather than the dashboard's `job_recovery.pause_job`
(status-preconditioned). See the 2026-08-10 entry above for why both routes
are intentional and NOT interchangeable.

# routes/jobs.py — Job 管理路由

## 为什么存在

Job 是一种带触发条件的任务（单次、定时、持续），由 `ModulePoller` 在后台轮询执行。这个路由暴露前端需要的 Job 操作接口：查列表、查详情、更新、取消、暂停、语义/关键词搜索、以及批量创建带依赖关系的 Job 群组（Job Complex）。2026-08-10 起，更新/暂停/两个搜索端点是 MCP 数据访问 seam 的 backend 半边——它们逐字复刻 `_job_mcp_tools.py` 里对应工具的调用序列，让非 agent 调用方（例如前端面板）拿到和 agent 自己调用工具完全一致的语义，而不是重新设计一套精简版接口。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(jobs_router, prefix="/api/jobs")`；前端 Jobs 面板
- **依赖谁**：
  - `JobRepository` — Job 的基础查询、状态更新、`pause_job`、BM25 关键词检索 (`search_keyword`)、多关键词检索 (`search_by_keywords`)
  - `xyz_agent_context.utils.db.db_factory.get_db_client` — 直接查询 `instance_jobs` 和 `module_instances` 表
  - `xyz_agent_context.module.job_module.job_service.JobInstanceService` — 创建 Job Complex 时同时创建 ModuleInstance 和 Job 记录；`update_job` 承担 PUT /{job_id} 的实际写入（含 append-to-payload、related_entity_id 的 diff sync）
  - `xyz_agent_context.module.job_module._job_scheduling.compute_next_run` — PUT /{job_id} 改 trigger_config 时原子重算 next_run 的 alpha/beta 对
  - `xyz_agent_context.module.job_module._job_response.job_to_llm_dict` — 两个搜索端点把 JobModel 整形成 LLM 友好 dict（复用同一份整形逻辑，保证 HTTP 调用方和 agent 看到同一套字段）
  - `backend/routes/_ownership.py` (`assert_owned`) — 新增四个端点的授权门；调用时机在 try/except 之外（见 2026-08-10 entry），失败直接抛 HTTPException，不落入本文件其他端点惯用的 `{"success": False, ...}` 200 shape

## 设计决策

**Job 依赖关系存在 `module_instances` 而非 `instance_jobs`**

依赖关系（`depends_on`）存储在 `module_instances.dependencies` 字段里，而不是 `instance_jobs` 表。列表查询时需要先拿到所有 job 的 `instance_id`，再批量查 `module_instances` 表，把依赖关系附加到 job 响应里。这是因为 Job 和 Module Instance 是 1:1 对应的，依赖是实例级别的概念，不是 job 级别的。

**Job Complex 的依赖解析**

创建 Job Complex 时，`task_key` 是用户用来表达依赖关系的临时标识，最终要转换成实际的 `job_id`。转换是顺序的：按 `request.jobs` 的顺序逐一创建，每创建一个就把 `task_key -> job_id` 记录下来，下一个 job 的依赖解析就能用到之前的映射。这意味着 `request.jobs` 的顺序必须是拓扑序（被依赖的 job 先出现）；否则解析时找不到 `task_key`，会报 "Invalid dependency" 错误。

实际上代码里会先校验所有 `task_key` 存在，但不做拓扑排序验证。如果 job A 依赖 job B，但 B 在请求列表里排在 A 后面，创建 B 时就能找到 A 的 job_id，但创建 A 时找不到 B 的 job_id——因为 B 还没创建。调用方必须自己保证顺序。

**`job_row_to_response` 的递归 JSON 解析**

`trigger_config` 和 `process` 字段可能被双重 JSON 序列化，代码里用递归函数 `parse_json_recursive` 反复 `json.loads` 直到得到期望的类型。这是数据写入时格式不一致的历史遗留问题。

## Gotcha / 边界情况

- **取消 running 状态的 Job**：处于 `running` 状态的 Job 不能被中断（Agent 正在执行中），但可以被标记为 `cancelled`，标记后 ModulePoller 不会再重新调度这个 Job。当前执行不会停止。
- **`status` 过滤的白名单**：列表接口对 `status` 参数有硬编码的有效值列表 `["pending", "active", "running", "completed", "failed", "blocked", "cancelled"]`。如果核心包里 `JobStatus` 枚举新增了状态值，这里的白名单需要同步更新，否则过滤会报 "Invalid status" 错误。
- **`format_for_api` 确保 UTC 时间格式**：`next_run_time` 等时间字段都通过 `format_for_api` 转换为带 `Z` 后缀的 ISO 8601 格式，以确保前端 `new Date()` 能正确识别为 UTC。
- **`assert_owned` 必须在 try 块之外调用**：当你把 `await assert_owned(...)` 放进本文件其它端点惯用的 `try: ... except Exception as e: return XxxResponse(success=False, error=str(e))` 块内时 → 症状是所有权拒绝（403/404/503）被吞成 200 + `{"success": false}`，调用方再也拿不到正确的 HTTP 状态码 → 根因是 `HTTPException` 是 `Exception` 的子类，会被泛化的 `except Exception` 一并捕获。四个新端点都是先调用 `assert_owned`，再进入 `try` 块。

## 新人易踩的坑

创建 Job Complex 时如果某个 job 创建失败，已经创建的 job 不会回滚。API 返回 `success=False` 和错误信息，但系统里已经存在部分创建的 job 群组。调用方需要自行处理清理逻辑。

`PUT /{job_id}` 的失败响应字段名不统一：正常业务失败（job not found、字段校验失败、无字段可改）用 `message`；只有顶层 `except Exception` 兜底才可能出现纯 `error`-style 情况——但本路由为了让 `job_id` 始终可见，兜底分支也返回了 `message`（携带 `job_id`），比 MCP 工具原版的兜底 `{"success": False, "error": str(e)}`（丢失 job_id）更有用，这是唯一一处刻意偏离工具原始 shape 的地方，记录在案以免被误当作 bug 修复掉。
