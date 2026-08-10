---
code_file: backend/routes/agents/narrative.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (round-3 review #1) — 链接上界修正 + truncated 标记

`_narrative_chat_history` 的 chat 扇出 N+1 修复(get_by_ids 循环→单次 get_by_ids)
之后,链接侧上界有两个坑,本次修:
- **过滤在截断之后**:`instance_narrative_links` 挂 aware_/social_/… 多类实例,
  原来先 `limit=200` 再过滤 chat_ 会在前 200 行多为非 chat 时**静默丢**聊天段。
  改为 `_MAX_NARRATIVE_LINKS=500` 抓行、过滤 chat_、再 `[:_MAX_CHAT_INSTANCES=100]`;
  命中上限时返回 `truncated=True`,不再无声截断。
- **无 ORDER BY**:LIMIT 不定序取的是引擎心情。加 `order_by="created_at DESC"`
  取最近的链接。
- 三个同值 `200` 字面量(链接行/消息/扇出)抽成语义各异的 `UPPER_SNAKE` 常量。
数据访问仍只走 `db.get`/`get_by_ids`(无裸 SQL)。round-4 补回归测试
`test_chat_history_survives_when_newest_links_are_non_chat`(前 200 链接全非
chat + 之后一条 chat,断言历史不丢)与
`test_over_cap_chat_instances_flag_truncated_and_fan_out_100`(>100 chat 实例
断言 truncated=True 且只扇出 100);fake db 真实现 order_by DESC——把代码回退
成旧的「limit=200 先取后过滤」两条测试即红(已验证),非形式测试。


## 2026-08-10 (pre-open review) — user_id 只信认证身份 + 扇出上界

- create 的 user_id 不再来自 body:assert_owned 只证明 agent 归属,
  body 里的 user_id 可把行归到任意用户名下——改由
  `resolve_current_user_id` 派生(本端点的写分支是本 PR 净新增,没有
  「忠实复刻」豁免)。
- `_narrative_chat_history` 的 chat 实例扇出截断到 100(共享 API 进程
  上无界 N+1 = 所有人的慢请求;MCP 孪生在模块进程里只慢自己)。
- title ≤300 / description ≤2000 上界。

# agents/narrative.py — Narrative endpoints for the MCP data-access seam (PR-2)

## 为什么存在

BasicInfoModule 的三个 narrative MCP 工具（`view_narrative` / `switch_narrative`
/ `create_narrative`，见 `_basic_info_mcp_tools.py`）目前直接拿数据库凭据、
在 MCP server 进程里跑原生 SQL。PR-2 的目标是给 AgentDataStore 一条 Http 路径
（HttpStore），让 mcp 容器不再需要数据库凭据；这个文件就是那三个工具的 Http
对应端点，数据操作和返回 shape 尽量与原工具对齐，但**不用原生 SQL**——路由侧
一律走 `AsyncDatabaseClient` 的 `get_by_ids`/`get` helper 或 service/repository
层，这条线的裸 SQL 只允许留在 agent 进程内部（数据库凭据本就在那）。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 聚合挂载到 `/api/agents`；未来
  HttpStore（PR-2 的 AgentDataStore Http 实现）会调用这三个端点，替代
  `_basic_info_mcp_tools.py` 里对 db 的直接访问
- **依赖谁**：
  - `backend.routes._ownership.assert_owned` — 每个端点先做 owner 校验
  - `xyz_agent_context.utils.db.db_factory.get_db_client` — 直接查
    `narratives`、`instance_narrative_links`、`instance_json_format_memory_chat`
    表（经 helper，非原生 SQL）
  - `xyz_agent_context.narrative.NarrativeService.create_narrative` —
    `POST /narratives` 的实际建表逻辑（同 `step_4_persist_results` 里
    `create_narrative` 信号被消费时调的方法）

## 设计决策

**`create_narrative` 端点直接创建，不是信号**

MCP 工具版本的 `create_narrative` 只是一个信号——它自己不写库，靠
`agent_runtime` 里 `step_4_persist_results._detect_narrative_routing_signal`
扫描 agent-loop 的 tool-call 输出、事后调 `NarrativeService.create_narrative`
真正建表。这个信号机制存在的原因是 MCP 工具进程和 runtime 进程是分开的，除了
"扫工具调用记录"没有别的方式通信。

这个 Http 端点没有这层进程隔离，也没有 runtime hook 可以托付，所以选择**直接
调 `NarrativeService.create_narrative` 建表**并把真实 `narrative_id` 返回给
调用方——如果也做成"只签收不建表"，调用方就永远拿不到 id，端点等于没用。

**`view_narrative` / `switch_narrative` 额外校验 `agent_id` 归属**

原 MCP 工具只按 `narrative_id` 查，不核对 `agent_id`（因为它跑在已知
agent 进程里，`narrative_id` 从对话上下文里来，天然属于当前 agent）。这两个
Http 端点路径上带了 `agent_id`，多做了一步 `row["agent_id"] == agent_id`
校验——`assert_owned` 只保证调用方拥有这个 agent，不保证 `narrative_id` 属于
这个 agent；不加这一步就是一个跨 agent 探测 narrative 内容的口子。不匹配时
返回和"narrative 不存在"完全相同的 `success=False` 错误文案，不额外泄露"这个
id 存在但不是你的"。

**`_narrative_chat_history` 用 helper 重写，不引用原函数**

`_basic_info_mcp_tools._narrative_chat_history` 用两条原生 SQL
（`SELECT instance_id FROM instance_narrative_links ...` /
`SELECT memory FROM instance_json_format_memory_chat ...`）。这里没有直接
import 复用它，而是用同样的过滤逻辑（`instance_id` 前缀 `chat_`、按
`meta_data.timestamp` 排序、`limit` 取最新 N 条）重新实现在
`db.get(...)` / `db.get_by_ids(...)` 之上——两处逻辑保持同步是手工纪律，不是自动
的；改一边记得看看另一边要不要跟。

## Gotcha / 边界情况

- **`view_narrative`/`switch_narrative` 对"narrative 不存在"和"narrative 属于
  别的 agent"返回同一个错误文案**：这是刻意的（见上面设计决策），不要在排查
  "为什么找不到 narrative"时假设两者可区分。
- **`create_narrative` 的 400-shape 是 200+success:false，不是 HTTP 400**：
  `title` 为空时不抛 `HTTPException`，走和其他失败一样的 200 响应体，和
  agents 家族其余端点（awareness.py 等）的失败 shape 保持一致。
- **`links` 里非 `chat_` 前缀的 instance（如 `aware_...`）会被过滤掉**：
  `instance_narrative_links` 表挂的不只是 ChatModule 实例，聊天记录只从
  `chat_` 前缀的 instance 拉，其余静默跳过。

## 新人易踩的坑

新增/修改这三个端点时，同步检查 `_basic_info_mcp_tools.py` 对应工具有没有变
——两边是"同一份产品语义、两个实现"，MCP 工具那边改了过滤规则或返回字段，这
里也要跟上，否则 HttpStore 和 DirectStore 路径会出现行为分叉（parity 测试的
坑，参照 `awareness.py.md` 2026-08-10 条目里 `create_missing` 的先例）。

## 相关约束

- `.mindflow/project/references/narrative_system.md` —— Narrative 选择/
  Instance-Narrative 绑定的全貌，理解 `instance_narrative_links` 语义时读
- 铁律 #21（placement rule）—— 本文件在 `backend/routes/`，只经 HTTP 被
  agent 进程消费，不被 `xyz_agent_context` 反向 import
