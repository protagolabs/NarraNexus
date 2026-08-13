---
code_file: src/xyz_agent_context/module/job_module/_job_writes.py
last_verified: 2026-08-11
stub: false
---

# _job_writes.py — 共享的 job 写实现

## 为什么存在（PR-8b）

`update_job_from_args` 是 job_update 那 ~90 行 build-updates 逻辑（effective_type
顺序、trigger_config+compute_next_run、next_run_time、status 校验 →
JobInstanceService.update_job）的**唯一**实现。它原本三份漂移：MCP 工具、
`backend/routes/jobs.py` PUT /{job_id}、以及本次新增的 agent-scoped seam 路由。
`effective_type` 在 compute_next_run **之前**解析是承重的 zombie-bug 修复（pre-open
review #4）：one_off→scheduled 换 cron 时必须用**新** type 算 next_run，否则算成
one_off→None、job 静默不再运行。合成一份是唯一不在某条 path 上重开这个 bug 的办法。

## 契约

方言安全（JobRepository/JobInstanceService，无裸 SQL）。返回完整 dict、从不抛。
agent-scoped：别的 agent 的 job 读作 "not found"（无存在性 oracle——旧 MCP 工具会漏
"does not belong to agent X"，本实现取路由的更安全措辞）。update/pause/cancel 失败键统一
`message`(+job_id)；create 失败键是 `error`（与读一致）。

## 2026-08-11 — 补齐 create/pause/cancel（job 3 写全迁 seam）

`create_job_from_args` / `pause_job_from_args` / `cancel_job_from_args` 落地，job 工具
不再持 `get_db_client_fn`——3 写全走 [[store]] AgentDataStore seam，mcp 容器可 strip
`DB_PASSWORD`。要点：

- **`setup_mcp_llm_context` 从工具搬进 `create_job_from_args`**。它跑相似标题 embedding
  dedup，需要 owner LLM 配置上 ContextVar，而那次配置加载**读 DB**。所以必须在**谁持 DB
  谁跑**：本地 DirectStore=mcp 进程、云端 seam 路由=backend（mcp 无凭据、加载不了配置）。
  留在工具里会在云端炸（无 DB）。W1 结构化错误兜底（LLMConfigNotConfigured=几乎总是猜错的
  agent_id→可行动重试；其余→结构化 dict）随之搬进 helper，从不抛。
- pause/cancel 沿用 update 的 **no-existence-oracle**（别的 agent 的 job = "not found"，
  比旧工具的 "does not belong to agent X" 更安全）；cancel 的实体解绑是 best-effort（记日志、
  绝不回滚已观察到的 cancel）。
