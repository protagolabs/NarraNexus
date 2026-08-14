---
code_file: src/xyz_agent_context/module/basic_info_module/_basic_info_mcp_tools.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (PR-7) — view_narrative/view_event/switch_narrative 迁走 seam

三个读工具改为 `get_agent_data_store().view_narrative/view_event/switch_narrative`，
数据访问下沉到 [[_narrative_reads]]（方言安全，DirectStore/backend 路由共用）。
本文件的**裸 MySQL 全删**（`SELECT \`trigger\``、`SELECT 1 FROM narratives`、
`_narrative_chat_history` 的手写 SQL），连同 `_parse_info`/`_narrative_chat_history`
两个 helper 及 `get_db_client`/`json`/`Any,Dict,List` import 一并清掉（死代码）。
**行为变化**：结果加 `success` 键（switch 旧 `ok`→`success`）、narrative 带
`truncated`、且**按 agent 归属过滤**（旧裸 SQL 能读别人的 narrative/event，跨租户）。
`create_narrative` 是纯信号（无 db），不迁。

## 2026-07-10 — submit_feedback 工具（Feedback 机制一期）

新增 `_register_feedback_tool`：`submit_feedback(agent_id, user_id, category,
summary, severity)`，Agent 觉察到用户不满、或同一指令连续失败 ≥2 次时调用。
经 [[feedback_client.py]] fire-and-forget 发到团队反馈接收端（写死 URL，
`NARRANEXUS_FEEDBACK_DISABLED=1` 可关）。隐私契约在 client 层强制：id 全部
哈希、只送 Agent 自己写的一句话摘要。工具恒返 ok=True——投递失败不该让
Agent 重试或向用户道歉。(设计记录为作者本地,不入库)


# _basic_info_mcp_tools.py — narrative-awareness MCP tools (Fix #2 P3)

## Why it exists

Gives the agent visibility + agency over the unified chat timeline built by
[[chat_module.py]] (`hook_data_gathering`). The system pre-picks a narrative for
each turn, but that pick is imperfect (esp. for short replies). These four tools
let the agent inspect threads/events and correct the routing.

## 上下游关系
- **被谁用**: the agent (LLM), via [[basic_info_module.py]]'s MCP server
  (`create_mcp_server` → `create_basic_info_mcp_server(port=7808)`).
- **依赖谁**: the read/validate data access now goes through the AgentDataStore
  seam (`get_agent_data_store()` → [[_narrative_reads]]); this file no longer
  imports `get_db_client` or writes raw SQL. `create_narrative` needs no db.

## 设计决策

**Two read tools, two signal tools.**
- `view_narrative(narrative_id)` / `view_event(event_id)` are reads that now
  route through the seam (DirectStore local / HttpStore cloud, via
  [[_narrative_reads]]); they return full thread history / full event detail
  (the timeline only shows a trimmed slice + the sent message). PR-7 replaced
  the old raw MySQL with dialect-safe, agent-scoped reads.
- `switch_narrative(narrative_id)` also routes through the seam (an
  existence+ownership validation). `create_narrative(title, description)` is a
  SIGNAL — validate/echo and return, no db, no migration. The MCP tool process and the agent_runtime are different
  processes; the runtime detects the tool CALL (args) in
  `_detect_narrative_routing_signal` and does the re-attribution in
  [[step_4_persist_results.py]] 4.0. `create_narrative` deliberately does NOT
  create in the tool (avoids double-create) — the runtime creates it from the
  {title, description} args. Keep the tool names in lockstep with step_4's
  detector (`SWITCH_NARRATIVE_TOOL` / `CREATE_NARRATIVE_TOOL`).

## Gotcha / 边界情况

- Tools take `agent_id` (+ `user_id` for create) as params, following the
  artifact-tool convention — the agent fills them from its instructions.
- `view_event` reads `events.event_log` (may be bytes) and truncates large
  fields to keep the tool result bounded.
