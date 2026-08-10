---
code_file: backend/routes/agents/jobs.py
last_verified: 2026-08-10
stub: false
---
## 2026-08-10 (PR-8b) — 新增 job_update 孪生端点 POST /{agent_id}/jobs/{job_id}/update

调共享 [[_job_writes]] `update_job_from_args`（与 DirectStore、前端 jobs PUT 同源）。
owner-gated；包 try 兜 get_db_client() → 200+{success:False,job_id,message}。


# agents/jobs.py — job 读 seam 孪生端点（agent-scoped, owner-gated）

## 为什么存在（PR-8）

JobModule 三个读工具的 byte-parity Http 孪生，让 HttpStore 无需 db 凭据。每个端点
调 [[_job_reads]] 的共享 fetch/search（与 seam 的 DirectStore 同一函数）→ 两路逐字
相同。owner-gated（`assert_owned`），挂 `/api/agents/{agent_id}/jobs`。

**与 `backend/routes/jobs.py` 区分**：那是前端向 job API（`/jobs` 前缀、response_model
形状、无 owner gate）；本文件是 seam 孪生。搜索用 POST（keyword 列表/过滤走 body），
by-id 用 GET。三个端点整体包 try 兜 `get_db_client()` 获取失败 → 200+{success:False,error}，
与 DirectStore 对齐。limit `Field(le=100)`，HttpStore 侧 `_clamp_limit` 预夹取避免 422。

**user_id 归属决定（预审 Important）**：搜索端点**故意接受**调用方传的 optional user_id（默认 None=该 agent 下全部用户 job），因为其调用方是 agent 本身（经身份转发 seam）查自己 agent 的 job，逐字保留工具签名以维持 Direct/Http parity。与 `backend/routes/jobs.py` 相反（那里 user_id 强制为登录用户，因为它是用户浏览器面，一个用户不能读另一个用户的 job）。两者对各自 actor 都对；assert_owned 把这些端点门控到 agent owner。
