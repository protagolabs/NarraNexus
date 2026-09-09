---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/_basic_info_mcp_tools.py
last_verified: 2026-09-09
stub: false
---


## 2026-09-09 — submit_feedback 加 (c) 平台注入凭据被拒触发 + 工具侧去重闸门

与 [[prompts.py]] Product Feedback Duty 第 3 条同口径：**平台注入的**凭据 / 端点 /
额度被平台工具拒绝时 `category=error` 上报，写明工具名与错误码；明确排除没绑定时的
`no_credential`、`official-agent-required` 类策略拒绝、用户刚输入的 secret 被拒；
同一次失败同时命中 (b)/(c) 时只按 (c) 记一条。

**为什么「一次」必须落在代码里**：触发 (a)/(b) 天然被人交互频率限住（用户得先抱怨 /
同指令得先失败两次），(c) 是机器生成的——平台侧故障在**每一次工具调用**上复现，且是
平台级而非单 agent 级。若只靠文案，一次 token 送错后端的事故就会给
`feedback_client` 灌几百上千条同义 `category=error`，团队反而看不见信号；而长
`agent_loop`（铁律 #14）上下文被压缩后，「我已经报过这个码了」是最先丢的事实，工具又
恒返回 ok=True，等于每次都在强化再报一次。

实现：可选参数 `dedup_key`（**必须可选**，`= ""`；改成必填会直接打断触发 (a)/(b)
的既有调用）。`_dedup_reserve` 在**发送前**占位（而不是发送后记录），关掉两个并发调用
同时通过的窗口；命中已占位则跳过 POST 并返回 `ok=True` +「Already filed this one」
——不能返回失败，工具契约要求 agent 不重试、不向用户道歉（见本文件 docstring 与
[[feedback_client.py]] 的 fire-and-forget 约定）。`_dedup_release` 在
`send_feedback` 返回 False 时撤销占位：否则缓存里存的就是**失败哨兵**，接收端一挂就
把这个 agent+错误码唯一的一次上报名额白白吃掉，事故永远报不出去；撤销后失败路径与去重
前的老行为一致。

**返回值承载「能对用户说什么」**（Owner 定调 2026-09-09）：`_feedback_result` 按
`category` + 真实投递结果构造消息，取代原来那句恒定的「Feedback recorded」。
- `delivered=False`（POST 挂了 / 本部署 `NARRANEXUS_FEEDBACK_DISABLED=1`）→
  `notified=False` + 明确禁止告诉用户「已通知团队」。这是本次真正的修复点：
  [[prompts.py]] 原文案让 agent 无条件宣称团队已被通知，而 [[feedback_client.py]]
  是 fire-and-forget、异常只进 DEBUG 日志，agent 根本无从知道有没有发出去。
- `delivered=True` 且 `category="error"` → `notified=True`，允许转述「团队已被通知」，
  但仍禁止断言原因。只有这一类会覆盖「默认不向用户提 telemetry」：用户正卡在平台侧
  故障上，「有人已经知道了」是我们唯一能给的真话。
- 其余 category → `notified=True` 但消息不提通知，维持默认不宣告。
- 去重命中 → `duplicate=True` + `notified=True`：占位只在**投递成功**后才留存
  （失败会 `_dedup_release`），所以命中即证明团队确实收到过。
恒为 `ok=True` 不变（工具契约：不重试、不道歉）。

边界：`dedup_key` 是调用方（模型）可控文本，故 key 截断到
`FEEDBACK_DEDUP_KEY_MAXLEN`、字典按 `FEEDBACK_DEDUP_MAX_ENTRIES` 上限 +
`FEEDBACK_DEDUP_TTL_SECONDS` 过期从头清扫（条目只插入不刷新，所以插入序即时间序）。
**单进程假设**（铁律 #20）：basic_info MCP server 是一个进程、全 deployment 共用，
本闸门**不跨进程、不跨重启**，也不替代接收端幂等——真正彻底的做法是把 dedup_key 带进
intake payload，但 intake 在本仓外、要先和团队约定字段。

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
[[chat_module.py]] (`gather`). The system pre-picks a narrative for
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

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
