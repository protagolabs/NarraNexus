---
code_file: src/xyz_agent_context/module/job_module/_job_writes.py
last_verified: 2026-08-10
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
"does not belong to agent X"，本实现取路由的更安全措辞）。失败键统一 `message`(+job_id)。
job_create 未迁（它用 LLM dedup + LLMConfigNotConfigured，backend 路由无 LLM context）；
job_pause/cancel 是简单写，另个 PR。
