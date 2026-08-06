---
code_file: backend/routes/agents/chat_history.py
last_verified: 2026-08-05
stub: false
---

## 2026-08-05 — `_drop_phantom_event_twins()`：旧副本行的读侧兜底

新增模块级纯函数，`get_chat_history` 在排序之后、`event_limit` 截断**之前**
调用（先滤后截，`event_limit` 才买到真实轮次）。

背景：2026-08-05 之前 [[step_4_persist_results]] §4.4 会把一轮对话复制成新的
`events` 行、每条"辅助 Narrative"一份。产生路径已删，但**已落库的行按铁律 #6
不做删除迁移**，所以在这里读侧过滤。

三条同时成立才丢，这正是副本的签名、且只有副本满足：

1. `event_log` 为空（副本从来没有过 log——复制发生在 run 收尾、源是内存 Event，
   而 §4.3 只回填 `final_output`）；
2. `final_output` 非空（有内容可复制）；
3. 集合里存在另一行，**输入相同 + `final_output` 相同 + `event_log` 非空**
   ——那就是正本。

因此：崩在第一步之前的真实轮次（无 log、也没有带 log 的孪生行）留下；同一个问题
再问一次也留下（重问有自己的 log、自己的回复）。**刻意不拿
`started_at IS NULL` 当判据**——只经过 `EventCRUD.create()` 的行天生如此，
生命周期列由 [[run_recorder]] 只写主 run 那一行（另外 Phase C 之前的历史行也
全是 NULL）。

用本地 2026-08-03 真实数据核对过：31 行 → 丢 12 行，每一行都命中
`completed + started_at NULL + 0 tool + event_log '[]'`，留下的 19 行里 0 行仍
带该签名。真机拉 `/chat-history?event_limit=0`：两个 agent 共 215 条，12 个已知
副本 id 一个都不在返回里，且剩下的重复 `final_output` 全是各自带 log 的独立轮次
（前 40 字相同而已）。测试：`tests/backend/test_chat_history_phantom_twins.py`。

**刻意留下的残余（已量化，不是漏网）**：如果**正本自己的 `event_log` 也是空
的**，副本就没有"带 log 的孪生行"可比，条件 3 不成立 → 留下。本地有 4 行属于
这一类（如 `evt_188705c45f7349ab`），它们的 `final_output` **全为空**，因此不
产生重复文字、也不产生独白气泡——用户真正报告的两件事都不受影响。
`started_at IS NOT NULL` 能把它们分开（只有正本会被 [[run_recorder]] 写这一
列），但那样也会丢掉"重问一次、崩了、回复恰好与前一轮相同"的真实轮次；**藏掉
真实轮次比多显示一个空行更糟**，故否决。另有反面样本证明条件 3 是必要的：
`evt_1e33fa7004d54298` 是**真轮次**（1 次工具调用、有真回复）却也存了空
`event_log`，它被正确保留。

## 2026-07-30 — meta carries the prompt-cache buckets

`_build_event_meta`'s cost_records aggregation now also sums
`cache_read_input_tokens` / `cache_creation_input_tokens` into
`EventLogMeta.cache_read_tokens` / `.cache_creation_tokens`. Same defect
class as the /costs popover fixed the same day: `input_tokens` is only the
full-rate bucket, so a cache-warm run's token chip showed "33 in" while
the model actually read ~869k. Buckets stay separate in the API; the
frontend sums for display ([[InnerThoughtCard.tsx]]).

## 2026-07-23 — event-log meta (activity card header)

`get_event_log_detail` returns `meta: EventLogMeta` assembled by
`_build_event_meta`: env_context.input (capped 4000 chars), lifecycle
(state/started/finished/derived duration — Phase C columns, None on
legacy rows), and a cost_records aggregation by event_id (distinct
models, token sums, total USD — None when no rows so the UI hides the
chip instead of lying with $0). Consumed by [[InnerThoughtCard.tsx]].
Tests: `tests/backend/test_event_log_meta.py`.

## 2026-07-10 — clear-history rebuilt as a scoped wipe delegating to wipe_service

`DELETE /{agent_id}/history` now takes `?conversations=&memory=` (default both
true; 400 if neither) and delegates to [[wipe_service.py]]
`wipe_agent_data(...)`. The old inline handler was incomplete — it deleted a
few DB tables but **never the on-disk narrative markdown / trajectories** (the
real memory; the DB is rebuilt from them on restart) and its session cleanup
globbed the wrong path (`{agent}_*.md` under the repo dir — the real files are
`~/.narranexus/sessions/{agent}_{user}.json`), so it deleted nothing. That glob
block is gone. Ownership is now enforced (agents.created_by, 404 for
non-owner) because `memory_*` is deleted by agent_id — a non-owner wipe would
destroy the owner's memory. `?user_id=` is rejected (TDR-12). The
"New-joiner traps" note below is superseded: the full table+disk set now lives
in `wipe_service`, not this route.

# agents/chat_history.py — 聊天历史与对话记录路由

## 为什么存在

这个文件暴露前端展示对话历史所需的所有读取接口：完整的 Narrative+Event 树（用于调试和归档视图）、简化的时序消息流（用于聊天界面）、单条 event 的 thinking/tool calls 详情（用于懒加载推理过程）。此外还提供清空历史的接口。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 聚合；前端聊天面板、历史记录页面、调试视图
- **依赖谁**：
  - `InstanceRepository` — 查询 ChatModule 实例
  - `xyz_agent_context.utils.db.db_factory.get_db_client` — 直接查询 `narratives`、`events`、`instance_narrative_links`、`instance_json_format_memory_chat`、`module_instances`、`cost_records` 表（**不含** `agent_messages`——那是墓碑表，见 [[agent_message_repository]] 2026-08-05）

## 设计决策

**两套查询路径的双重 fallback**

`get_chat_history` 有两条查询路径。主路径通过 ChatModule 实例的 `instance_narrative_links` 关联找 Narrative，更准确；fallback 路径通过 `narrative_info.actors` 字段过滤，是老版本的查询方式。如果主路径找不到任何 Narrative（比如老数据），自动降级到 fallback。这个设计是为了向后兼容历史数据，因为早期版本没有 instance-narrative 关联表。

**`simple-chat-history` 绕过 Narrative 层**

简化聊天记录接口 (`/simple-chat-history`) 不走 Narrative，直接从 `instance_json_format_memory_chat` 表读取 ChatModule 的 message 数组。这比 Narrative/Event 路径更高效，也更贴近"展示最近 N 条消息"的使用场景。分页用"从最新往旧"的方向切片，而不是传统的 offset/limit，因为聊天界面通常先显示最新消息。

**清空历史的多表级联**

`clear_conversation_history` 不只删 `narratives` 和 `events`，还清理
`instance_json_format_memory_chat` 和 sessions 目录下的文件。这是因为聊天历史
实际上分散在多个存储里，只清一个的话前端会看到数据不一致。（`agent_messages`
也在删除清单里，但那是墓碑表、恒为 0 行——不是聊天正文所在处，见
[[agent_message_repository]] 2026-08-05。上面 2026-07-10 段说明完整清单现在
在 [[wipe_service]] 里，不在本路由。）

**event log 的 thinking 重组**

Event 的 `event_log` 字段里存的是流式 delta，每个 thinking_delta 是一条独立记录。`get_event_log_detail` 需要把这些 delta 拼接成连贯的 thinking 块，遇到 tool_call 等中断时开启新块。这是懒加载推理详情时在服务端做的重组。

## Gotcha / 边界情况

- **non-chat working_source 的消息过滤**：`simple-chat-history` 对 `working_source != "chat"` 的消息只保留 assistant 角色的消息，过滤掉 user 消息。这是因为 job/matrix 触发的 user 消息是系统生成的触发提示，不应该展示给用户。如果将来有新的 working_source 类型，需要检查这个过滤逻辑。
- **分页方向**：`simple-chat-history` 的 `offset` 参数是"从末尾跳过 N 条"，而不是传统的"从开头跳过 N 条"。`offset=20, limit=20` 取的是倒数 21-40 条，而不是正向的第 21-40 条。
- **timestamp 解析的多格式兼容**：`_parse_timestamp` 需要处理 MySQL datetime（带或不带时区）和 SQLite 文本格式，代码里有一个多格式 fallback 列表。这说明历史数据里存在时间戳格式不一致的情况。

## 新人易踩的坑

删除聊天历史时，不能只删 `narratives` 和 `events` 表。聊天正文在
`instance_json_format_memory_chat`（ChatModule 的 per-instance JSON 记忆），
不清理会导致下次启动时 Agent 仍然能"记住"被删除的对话。完整清单在
[[wipe_service]]。

**别把 `agent_messages` 当聊天正文**：它没有写入者、恒为 0 行。0802
【对话时序错乱】的分析就是查了这张表发现是空的、于是推断聊天面板在回放
`events`——两个结论都错。`/simple-chat-history`（聊天面板）读的是
`instance_json_format_memory_chat`；`/chat-history`（Narrative / Runtime 面板）
读的才是 `events`。见 [[agent_message_repository]] 2026-08-05。
