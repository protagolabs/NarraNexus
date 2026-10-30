---
code_file: frontend/src/lib/defaultAgent.ts
last_verified: 2026-10-30
stub: false
---

# defaultAgent.ts — 没有有效选中时选谁

`/api/auth/agents` 返回查看者自己的 agent **加上全平台所有公开 agent**，按创建时间
倒序，所以 `agents[0]` 往往是陌生人最新创建的公开 agent。过去登录页和侧栏挂载时
默认选它，各面板随即以非 owner 身份请求，成串 403——正是边缘监控 v2 按失败数封 IP
的信号。规则：取 `created_by === userId` 的第一个（即自己最新的），没有就 `''`
（不选，显示空态）。调用方：[[LoginPage]]（两条登录路径）、[[AgentList]]（挂载
兜底）。`configStore.refreshAgents` 清掉失效选中后，也靠 AgentList 下次挂载走这里。
local / Manyfold 模式下 `created_by` 与 `userId` 是同一值（Manyfold 的
`created_by = nx_user_id`），规则同样成立。
