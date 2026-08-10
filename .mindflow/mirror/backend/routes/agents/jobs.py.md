---
code_file: backend/routes/agents/jobs.py
last_verified: 2026-08-10
stub: false
---

# agents/jobs.py — job 读 seam 孪生端点（agent-scoped, owner-gated）

## 为什么存在（PR-8）

JobModule 三个读工具的 byte-parity Http 孪生，让 HttpStore 无需 db 凭据。每个端点
调 [[_job_reads]] 的共享 fetch/search（与 seam 的 DirectStore 同一函数）→ 两路逐字
相同。owner-gated（`assert_owned`），挂 `/api/agents/{agent_id}/jobs`。

**与 `backend/routes/jobs.py` 区分**：那是前端向 job API（`/jobs` 前缀、response_model
形状、无 owner gate）；本文件是 seam 孪生。搜索用 POST（keyword 列表/过滤走 body），
by-id 用 GET。三个端点整体包 try 兜 `get_db_client()` 获取失败 → 200+{success:False,error}，
与 DirectStore 对齐。limit `Field(le=100)`，HttpStore 侧 `_clamp_limit` 预夹取避免 422。
