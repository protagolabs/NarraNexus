---
code_file: src/xyz_agent_context/message_bus/local_bus.py
last_verified: 2026-08-12
stub: false
---
## 2026-08-07 (二次) — 抑制谓词改问「这棵树里有人被停吗」(PR #252 review Critical #1)

初版是 `LEFT JOIN events ON m.root_run_id = e.event_id` + 判断**那一行**的
`cancel_requested_at` —— 即只看**根**有没有被盖旗标。而端点只给 running 的
行盖旗标(终态行绝不盖,否则会成为下一个 run 的陷阱)。

失效场景恰好是委派的**主流形态**:agent 把活交给同伴后自己这一轮就结束
(「发出去、结束本轮、对方稍后回」)。owner 在 roster 上看到同伴在跑、点
停止时,根那一行早已 `completed` 且永远不会被盖旗标 → 谓词读成「没人被
停」→ 排队消息照常投递 → 正是本功能要消灭的打地鼠。

改为相关子查询 `NOT EXISTS (SELECT 1 FROM events e WHERE e.root_run_id =
m.root_run_id AND e.cancel_requested_at IS NOT NULL)`:问的是**整棵树**有没有
任何一个 run 被请求停止,与哪一行是根、根是什么状态都无关。

初版的测试全部只覆盖「根仍 running」,所以这个洞不会红 ——
`test_a_stopped_tree_whose_root_already_finished_still_suppresses` 补上了这
一支。这条 SQL 走 RAW backend(**无 `%s→?` 翻译**),是本次方言风险最高的
一句,已在真 MySQL 上验证(`tests/message_bus/test_cascade_stop_mysql.py`)。

## 2026-08-07 — 被停止的树,排队消息不再唤起 run

`get_pending_messages` 增加 `LEFT JOIN events ON m.root_run_id = e.event_id`
+ `AND (m.root_run_id IS NULL OR e.cancel_requested_at IS NULL)`。

停掉正在跑的轮次**不够**:它们排队中的后续消息会在下一次轮询启动新 run,
用户按了停之后眼看着新活冒出来(设计文档里的"打地鼠")。

- **写成 SQL 谓词而不是逐行判断**:poison 过滤已经是每行一次查询,再加一次
  就是第二个 N+1。
- **NULL root 永不被压制**:用户消息和所有前置列的老行都是 NULL,若 NULL 被
  当成"同一棵树",一次停止会让整张表哑掉。测试专门钉了这条。

`send_message` / `send_to_agent` / `_row_to_message` 同步接受并透出
`root_run_id`。

## 2026-08-04 — send_message/send_to_agent 记录发送方 turn 的种类

新增可选 `sender_turn_source`,落到 `bus_messages.sender_turn_source`:
owner 面 turn(chat/job/…)= 发送方在跑差事、这条是**提问**;`message_bus`
= 它本来就在答同伴、这条是**回复**。[[message_bus_trigger]] 据此选给收件方
哪条指令 —— 靠 channel 排序推断做不到,因为 DM channel 是**对称查找+复用**
的(见本文件 `send_to_agent` 的 direct-channel 查询),opener 永远固定而
差事是双向跑的。`_row_to_message` 同批带出该字段。

## 2026-07-31 — send_message persists event_id

`send_message` gained `event_id: Optional[str] = None`; persisted on the
`bus_messages` row and surfaced by `_row_to_message`. Only the trigger's team
branch passes it (the turn that produced the reply); every other caller keeps
the default None.

## 2026-07-28 — batched room pending summary + the poison threshold moves here

`get_room_pending_summary(channel_id, agent_ids)` answers "what is still waiting
for each of you in THIS room" in three queries, independent of member count.
[[teams]]'s activity block previously called `get_pending_messages` per member,
which is one query per member PLUS one `get_failure_count` per pending row — a
6-member room polled every 3s ran ~30 queries a tick, forever, just to render a
status chip.

It deliberately re-implements the same notion of pending (past the member's
cursor, not self-sent, poisoned rows excluded) restricted to one channel and to
@-addressed messages. The cursor comparison happens in Python via `_as_utc`
because the backends disagree on the wire type: MySQL returns naive
`datetime`, SQLite returns the ISO strings `_now_iso` wrote.

`POISON_FAILURE_THRESHOLD` now lives here — `get_pending_messages` is the filter
that enforces it, and it used to be a bare `3` here with a same-valued constant
in [[message_bus_trigger]] whose comment asked a human to keep them in sync.


## 2026-07-22 — get_recent_messages (newest N, chat order)

Added `get_recent_messages(channel_id, limit=20)` — `ORDER BY created_at DESC LIMIT n`
reversed to oldest→newest. `get_messages` is ASC-limited (the OLDEST n), wrong for "recent
scrollback"; this powers the team-room prompt's history window (see [[message_bus_trigger]]).

## 2026-07-20 — multimodal: messages can carry file attachments

`send_message` / `send_to_agent` gained an `attachments: list[dict] | None` param;
`_row_to_message` deserializes the new `bus_messages.attachments` JSON column. When
files are present the `msg_type` auto-flips `text`→`multimodal` (UI/search hint).
Attachments travel by REFERENCE (metadata + base-relative shared-area path), never
bytes — staging + marker synthesis live in [[_bus_attachment_impl]]. No change to
the same-user boundary: files ride the same DM/channel path, so cross-user still
raises `PermissionError`. Insert still goes through the dialect-safe `db.insert`.

## 2026-07-02 — poison threshold now has a detection + recovery path (no code change here)

This file's own behavior is unchanged, but the `failure_count < 3` filter in
`get_pending_messages()` (see Design decisions below) now has two consumers
that didn't exist before: `MessageBusTrigger._notify_permanent_failure`
(`message_bus_trigger.py`) writes an inbox notice once a message's
`bus_message_failures.retry_count` reaches 3, and
`backend/routes/agents/bus_failures.py` lists/clears those rows so the
message is picked back up on the next poll. Neither talks to
`LocalMessageBus` directly for the recovery path — the retry route deletes
the `bus_message_failures` row via a fresh `AsyncDatabaseClient`, not this
class — see that file's mirror md for why.

## 2026-06-24 — raw SQL must use BARE identifiers, not double quotes (MySQL gotcha)

`get_messages` and `get_channel_members` had used double-quoted identifiers
(`SELECT * FROM "bus_messages" WHERE "channel_id" = ?`). SQLite accepts `"..."`
as an identifier quote, but MySQL (prod/dev, no `ANSI_QUOTES`) treats it as a
**string literal** → `ProgrammingError 1064` syntax error. These queries were
latent since the pluggable-DB-backend commit and only surfaced when team group
chat became the first cloud-mode caller of these methods (silent UX: the
team-chat POST 500'd and the composer restored the draft). Fixed to bare
identifiers (`FROM bus_messages WHERE channel_id = ?`), matching every other raw
query in this file. **Rule: any `db.execute` raw SQL here must be dialect-safe —
bare identifiers + `self._db.placeholder`, never `"`-quoted names.** (Same fix
applied to `_team_cascade_depth` in [[message_bus_trigger]].)

## 2026-06-23 — ack_processed canonicalises the cursor timestamp

`ack_processed` now does `up_to_timestamp = up_to_timestamp.isoformat()` when
given a `datetime`, so the stored cursor and `bus_messages.created_at` (both
TEXT, compared lexicographically in `get_pending_messages`) always share the
isoformat `…T…+00:00` shape. Previously a `datetime` could be persisted as
`str()` space-format (`"… …+00:00"`); since 'T' > ' ', every newer message then
looked unprocessed and the agent re-triggered forever. See the matching note in
`message_bus_trigger.py.md` (the call sites also dropped their `str()` wraps).

## 2026-06-08 — bus message search index (projection)

`send_message` now also writes a `memory_bus` index (message content + `source_ref`→bus, tagged `channel:<to>`) so inter-agent messages are findable via `remember`. Append-only — one index per message, no update/dedup (same nature as chat history). Best-effort.

# local_bus.py — MessageBus 的 SQLite/MySQL 实现

## 为什么存在

`MessageBusService` 抽象接口的本地实现。名字叫 "local" 但实际上支持任何 `DatabaseBackend`（SQLiteBackend 和 MySQLBackend 都可以）——"local" 的含义是"非云端 API"，即所有状态存在本地数据库而非远程消息队列服务。

这是生产环境实际运行的实现，`CloudMessageBus` 还只是占位。

## 上下游关系

**被谁实例化**：`message_bus_trigger.py` 里的 `_get_bus()` 工厂函数通过 `LocalMessageBus(backend=db._backend)` 创建实例；`module/message_bus_module/` 的 MCP 工具初始化时也需要一个 `LocalMessageBus` 实例。

**依赖谁**：接受一个 `DatabaseBackend` 实例（不是 `AsyncDatabaseClient`，是更底层的后端接口）；通过 `backend.execute()` 执行原始 SQL；序列化/反序列化 JSON 用标准库 `json`。

## 设计决策

`get_pending_messages()` 的 SQL 逻辑：`created_at > last_processed_at`（或 last_processed_at IS NULL）AND `from_agent != agent_id`（不处理自己发的消息）AND 失败次数 < 3（poison message 过滤）AND 该 Agent 是频道成员（JOIN `bus_channel_members`）。

`send_to_agent()` 的自动创建逻辑：检查两个 Agent 之间是否已有 direct channel（名称用排序后的 agent_id 组合，保证对称唯一），不存在则创建，然后把两个 Agent 都加为成员，最后发消息。这是幂等的——并发发消息可能触发竞态创建两个 channel，但查询逻辑取的是特定名称，后续调用会找到已存在的。

`_generate_id()` 用 `secrets.token_hex(4)` 生成 8 字符的 hex，与系统其他 ID 的生成方式略有不同（其他地方用 `uuid4().hex[:8]`）。功能等价，但格式上 `secrets.token_hex` 是纯十六进制，`uuid4().hex` 也是十六进制——实际是一样的。

## Gotcha / 边界情况

`LocalMessageBus` 接受 `DatabaseBackend` 而不是 `AsyncDatabaseClient`——这个区别很重要。`DatabaseBackend` 是更底层的接口，直接支持 `execute()` 方法。如果你有 `AsyncDatabaseClient` 实例，用 `client._backend` 取底层 backend。

`ack_processed()` 用 UPSERT 逻辑更新 `bus_channel_members.last_processed_at`——如果成员记录不存在（agent 只是消息接收者但不是正式频道成员），这里可能会失败或无效。Agent 必须先通过 `join_channel()` 成为正式成员，`last_processed_at` 游标才能被正常追踪。

## 新人易踩的坑

所有 SQL 里用的是 `%s` 占位符（不是 `?`），这依赖 `DatabaseBackend.execute()` 的参数处理层把 `%s` 自动转成目标数据库的占位符格式。不要改成 `?` 或 f-string 直接拼接。

## 2026-08-11 — 未读的三个契约:`ack_read` / `get_unread(limit)` / `count_unread`

`get_unread` 此前**没有契约**,三个缺陷叠在一起:

1. **不排除自己发的帖。** `get_pending_messages` 从写下来就有 `from_agent != me`,
   这条一直没有。于是 agent 在活跃房间里把自己说的话当成别人的待回消息读回来。
2. **返回最旧 N 条。** `ORDER BY created_at ASC` + 无 SQL LIMIT + 调用方 Python 切片。
   `get_recent_messages` 早就把正确形状(DESC + `reversed()`)连同理由写在 docstring 里
   了,这里没采纳。配上永不推进的读游标,每一轮拿到的是**同一批最古老的消息**,却被
   当作"房间现在的动静"呈现。
3. **无 LIMIT**,整个积压过一趟网络再被切掉。

现在 `limit` 选**最新的 N 条**、仍按阅读顺序返回;`limit=None` 保留全量模式,
**这不是可选项**:模块的 turn 后钩子要拿全量来判断"这一轮的回复覆盖了哪些消息"。
给它一个窗口,一个安静频道就可能被繁忙频道整个挤出窗口 —— agent 回了那个频道,
游标却一动不动,它刚回答过的消息永远留在未读里,下一轮再被要求回答一次。
`test_get_unread_contract.py` 里有一条专门的看门狗钉这个形状。

`count_unread` 是新的:渲染是 `N unread (showing M)`,查询一旦加了 LIMIT,N 就不能
再从结果 `len()` 来 —— 那样 N 恒等于 M,读者永远不知道自己看的是个窗口。

`ack_read` 是 `ack_processed` 在另一根游标上的孪生。两根游标语义不同、不能合并
(`inbox.py` 合并过一次,后果见那份 mirror)。时间戳归一化直接委托给 `ack_processed`
的同一段逻辑而不是重写一遍 —— 那个 `'T'(0x54) > ' '(0x20)` 的坑值不得踩第二次。
它**只前进**:`ack_processed` 能不带这个保护是因为它的调用方总是传批次自己的水位线,
而 `ack_read` 有多个调用点,一个能被往回拨的游标会让已读消息重新冒出来。

## 2026-08-11 (补) — `canonical_ts`:那个坑只留一个落点

两个 ack 各自抄了一遍 `isoformat()` 归一化,而 `ack_read` 的 docstring 却写着它
"delegated" 给了 `ack_processed` —— 注释和代码不一致,偏偏这是个踩过的坑
(`'T'(0x54) > ' '(0x20)`,空格格式的游标沉到所有 `created_at` 之下,消息永远显示
未处理)。抽成模块级 `canonical_ts`,两个 ack 和 trigger 的缺口判定共用一份。

## 2026-08-12 — `segments` 落库与读回

`send_message` 追加 `segments`（**追加，不插入**——存在位置参数调用方，中间插一个会静默重绑，
本仓库在 `ContextRuntime` 上已经付过这笔学费；有测试钉住它在参数表末位）。

- 空列表存 NULL：它看起来像数据，会诱使读者相信「这轮确实没有分段」；NULL 明确表示「没有记录边界」。
- **坏 JSON 不炸整个房间**：一行手改坏了只丢那条消息的排版（降级），
  而不是让整个 transcript 打不开（故障）。会记 warning，否则就是静默降级。
