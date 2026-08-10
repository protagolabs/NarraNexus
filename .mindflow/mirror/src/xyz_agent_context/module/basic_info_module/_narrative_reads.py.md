---
code_file: src/xyz_agent_context/module/basic_info_module/_narrative_reads.py
last_verified: 2026-08-10
stub: false
---

# _narrative_reads.py — 共享的方言安全 narrative/event 读

## 为什么存在（PR-7）

basic_info 的 `view_narrative`/`view_event`/`switch_narrative` MCP 工具原本是**裸
MySQL**（``SELECT `trigger` … FROM events``、`SELECT 1 FROM narratives`、
`instance_narrative_links` 手写 SQL），只靠本地 sqlite 翻译垫片能跑，是双方言纪律
禁止外泄的东西。本模块把这些读**重写在 `AsyncDatabaseClient` 的 get_one/get/
get_by_ids 上**（SQLite+MySQL 通用）。

`fetch_narrative_view` / `fetch_event_view` / `check_narrative_switch` 各自返回
**完整结果 dict 且从不抛异常**——所以 AgentDataStore 的 DirectStore 与 backend
[[narrative]] 路由都能直接 `return await fetch_x(...)` 拿到**逐字相同**的输出
（parity=单一实现，不是两份手抄）。`narrative_chat_history` 从 narrative.py 提上来
共享（去重）。

## 安全：补上 agent_id 归属过滤

旧裸 SQL 按 id 查，**不校验归属**——任何 agent 传另一个 agent 的 narrative_id/
event_id 就能读到别人的内容（跨租户读）。这里的每个函数都加了
`row.get("agent_id") != agent_id → not found`（event 直接进 get_one 过滤），把读
限定在调用方自己。

## 形状（比旧工具 enrich）

统一加 `success` 键（旧 view_* 无 success、switch 用 `ok`），narrative 视图带
`truncated`（chat 实例扇出触顶时不静默丢历史，铁律 #16）。`event_log` 是**原始**
步骤轨迹串（截断 20000），不是前端 event-log 路由解析出来的 thinking/tool_calls。
