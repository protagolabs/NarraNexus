---
code_file: backend/routes/agents/profile.py
last_verified: 2026-08-10
stub: false
---

# agents/profile.py — update_agent_profile 的 seam 孪生路由（agent-scoped, owner-gated）

## 为什么存在（PR-9）

`POST /api/agents/{agent_id}/profile/update` 是 update_agent_profile 工具的 byte-parity Http
孪生，让 HttpStore 无需 db 凭据也能跑改名事务。与 seam 的 DirectStore 调**同一个**
[[_awareness_writes]] `update_agent_profile_from_args`。owner-gated（assert_owned）。

## 消息信封（区别于 awareness 路由）

工具返回**动态**状态串（改了哪些字段 + 重名 note），不是 awareness 那种定值常量——所以本端点
不走「结构化响应 + HttpStore 重建常量」那套，而是把工具串**原样**放进 `{"message": <str>}`
信封，[[store]] HttpStore 拆信封原样返回。handler 对已处理结果恒 200；get_db_client 失败兜成
`{"message": "Error: ..."}`。挂载在 [[core]]。
