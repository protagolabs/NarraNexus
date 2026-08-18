---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_4_persist_results.py
last_verified: 2026-08-05
stub: false
---

## 2026-08-05 — §4.4 一轮只写一行 Event（0802「对话时序错乱」根因）

§4.4 原来对 `ctx.narrative_list[1:]`（辅助 Narrative）逐条调
`event_service.duplicate_event_for_narrative(ctx.event, narrative.id)`，
把同一轮对话**复制成新的 `events` 行**。三处叠加成了用户看到的乱序：

1. 复制发生在 run 收尾，源是**内存里的** `ctx.event`——§4.3 只把
   `final_output` 同步回内存，`event_log` / `module_instances` 从未回填。所以
   副本行是 `event_log='[]'`、`module_instances='[]'`。
2. 生命周期列由 [[run_recorder]] 只写主 run 那一行（`run_id` = 主 event id），
   副本行因此永远 `state='completed'` + `started_at IS NULL` +
   `tool_call_count=0`。
3. 副本的 `created_at` 是 run **结束**时刻，不是提问时刻。

于是从 `events` 表回放的界面（[[chat_history]] `/chat-history` → 前端
[[NarrativeList.tsx]] / [[AwarenessPanel.tsx]]）把同一轮显示最多 3 次，且排在
更新的对话**下面**——"已经回答过的老问题又冒出来"，`final_output` 那段独白也成了
独立条目。这就是 0802 现场多人复现的签名（实锤 08:21:08.370/.403 两组；本地
2026-08-03 数据同样可复现，31 行里 12 行是副本）。

**现在的语义**：一轮对话**只被写入一条线程** = `narrative_list[0]`（经 §4.0
routing 后的头部）。`narrative_list[1:]` 是 step_1 为了拓宽**读侧**上下文而按
BM25 拉进来的邻居（`MAX_NARRATIVES_IN_CONTEXT=3`），它们通过把**同一个
event id** 追加进各自的 `narratives.event_ids` 建立关联——多对多本来就该放在
这个列表列上，邻居线程仍能通过 `select_events_for_context` 回放这轮对话。
`events.narrative_id` 保持单值。`update_event_narrative_id` 移出循环、只调一次。

`duplicate_event_for_narrative` / `EventCRUD.duplicate` 一并删除（铁律 #2，不留
休眠接口）。已落库的旧副本行按铁律 #6 不做删除迁移，由 [[chat_history]] 的
`_drop_phantom_event_twins()` 在读侧兜底。

**顺带修掉同族的第二种重复：一条线程被访问两次。** §4.0 的 `switch_narrative`
把 `narrative_list[0]` 指向 agent 点名的那条线程，而那条线程**可能本来就在
列表里**（BM25 也把它当邻居捞进来了）。于是循环会撞到同一条 narrative 两次：
修复前多产出一行副本，修复后仍会往它的 `dynamic_summary` 里追加**第二条一模
一样**的条目。现在按 narrative id 去重（保序），进度消息里的
`Narratives=` / `narratives_updated` 也改报去重后的条数。真实证据：本地
`evt_f590aef867f14187` 这行副本的 `narrative_id` 与它自己的正本相同。

测试：`tests/agent_runtime/test_step4_event_attribution.py`（真 SQLite 驱动整个
step_4，断言 `events` 只有一行、无幽灵签名、三条 Narrative 都引用同一个 id）。

## 2026-08-04 (review 修正) — 锚点判定改用 owner-visible 谓词

`_turn_delivered_user_message` 由 `extract_reply_text` 改为
`extract_owner_visible_text`：bus 轮对 peer agent 的交付不再触发
proactive-delivery 分支（否则每次 A2A 回复都会把 owner 的
current_narrative_id 指到 bus narrative 并清空 last_query——PR #230
review Important #2）。函数 docstring 的契约（"surfaces in the user's
chat"）从此与实现一致。测试：
tests/agent_runtime/test_owner_visible_delivery.py。

## 2026-07-29 — 删除 4.7 句柄持久化(T5),−96 行

`_persist_cli_session_handle` 及其调用点删除。

原来那个位置约束(4.7 必须在 4.0 和 4.5 之后)是承重的:句柄锚定在
`ctx.session.current_narrative_id`,而 4.0 的中途 `switch_narrative` /
`create_narrative` 和 4.5 的主动投递都可能重新指向它,存"路由后"的叙事才能让锚点
匹配对话实际延续的那条线。

现在**没有句柄要存、也没有锚点要对齐** —— 见 [[transcript]]。这条约束消失的直接
后果:中途切叙事不再导致下一轮冷启动。

## 2026-07-28 — [4.7] 存的是 **routing 之后**的 narrative：刻意、fail-open（review FIX 2）

**零行为改动，纯文档 + 回归钉。** review 指出：step_3 用**轮前**的
`session.current_narrative_id` 校验句柄，而 4.0（agent 中途调
`switch_narrative` / `create_narrative`）会在 4.7 落库**之前**改写它，因此存进
`agent_cli_sessions.narrative_id` 的可能不是当初通过校验的那个 narrative。

结论是**接受并写清楚**，不改行为：

- 存储的 `narrative_id` 只被 step_3 校验闸门当等值锚用，不一致时 **fail-open**
  ——下一轮读到的 `session.current_narrative_id` 已经是被路由到的那个：
  会话继续留在新线程 → 两边**相等**，resume 正常（这也正是想要的：CLI 会话
  确实延续进了那个线程的这一轮）；若哪天不等 → 日志
  `COLD reason=narrative_changed` + 冷启动，代价仅**一次冷启动**，绝不会在
  未获批准的 narrative 下 resume。
- 反过来存**routing 之前**的 narrative 只会让每个被路由的轮次都必然错配，
  即严格更多冷启动、correctness 上零收益。
- 注释落在 `_persist_cli_session_handle` docstring + 4.7 调用点（后者把
  "4.7 必须排在 4.5 之后"扩成"**4.0 与 4.5 之后**"）。
- 钉子：tests/agent_runtime/test_resume_narrative_routing.py —— 用真 SQLite
  驱动整个 step_4（含 4.0），断言三件事：存的是 routed narrative；下一轮留在
  routed 线程则 RESUME；下一轮回到原 narrative 则 None +
  `COLD reason=narrative_changed`。

## 2026-07-28 — [4.7] 抽为 `_persist_cli_session_handle` + resume_failed 删旧写新（resume 化 R3）

4.7 整段抽成模块级 `_persist_cli_session_handle(ctx, execution_result)`
（可单测；调用点一行）。新语义：`execution_result.resume_failed` 为真 →
**先 `delete_handle` 删陈旧行**——即使冷启动重试没报新 cli_session_id 也删，
否则下一轮还会踩同一具尸体——再按新 cli_session_id 正常 upsert（重试产生的
是**新** session_id，删旧写新一步完成）。守卫拓宽为
`(cli_session_id or resume_failed) and ctx.session`。清句柄只在
orchestrator 侧做：适配器跑在 Executor 容器里没有 DB。fire-and-forget 契
约不变（任何失败仅 warning）。测试：
tests/agent_runtime/test_step4_cli_handle_persistence.py（真 SQLite schema）。

## 2026-07-25 — 新增 [4.7] CLI 句柄落库(resume 化 R1)

4.6 之后:`execution_result.cli_session_id` 且 `ctx.session` 存在 → upsert
`agent_cli_sessions`(经 [[cli_session_repository]])。narrative_id 取
`ctx.session.current_narrative_id`——**4.7 必须排在 4.5 之后**:proactive 分支里
4.5 会先把 session 锚点重指到 main_narrative,4.7 随后读取天然一致。指纹/
working_path 由 step_3 随 PathExecutionResult 带出,这里不重算;两者任一缺失
(step_3 fail-open)→ 跳过并 warning,不落半残行(表列 NOT NULL)。整段
try/except + warning-only,照 4.6 的 fire-and-forget 风格,永不阻断管线。

## 2026-07-23 — [4.6] record_cost 透传 cache/num_turns(W1,纯搬运)

`record_cost(...)` 调用新增 `cache_read_tokens`/`cache_creation_tokens`/`num_turns`
三个实参(来自 execution_result)。fire-and-forget 契约不变。

## 2026-06-08 — interaction index (chat+event merge)

After the event's `final_output` is set, step 4 writes ONE interaction index into `memory_event` via `MemoryEngine.index('event', event_id, user_input + final_output)` — the per-turn searchable conversation surface, with a `source_ref` back to the event. This is the chat/event merge: `remember` finds the interaction, then the agent fetches the full agent-loop trace via `view_event` (or the conversation context via `get_chat_history`). Best-effort (an index failure never breaks persistence). It replaces the retired `memory_chat` search-mirror that ChatModule used to write (see [[chat_module]]).

## 2026-05-20 (Fix #2 P3) — 4.0 narrative routing signal

New step 4.0 honors the agent's switch_narrative / create_narrative tool calls
(basic_info MCP — see [[basic_info_module.py]]). `_detect_narrative_routing_signal`
scans `execution_result.agent_loop_response` for the LAST such call; on switch it
loads the target narrative, on create it makes one via
`narrative_service.create_narrative`. The target then becomes the head of
`ctx.narrative_list` (note: `ctx.main_narrative` is a read-only property over
`narrative_list[0]`, so we override the list, not the property) + the local
`main_narrative`, so the event attribution (4.4), markdown stats (4.2), summary
updates, and the session anchor (4.5) all flow to it; `session.current_narrative_id`
is repointed so the NEXT turn continues there. It ALSO re-binds THIS turn's chat
persistence: step_5's ChatModule hook writes to the module object's
`self.instance_id` (bound in step_1 to the ORIGINAL narrative's chat instance),
so 4.0 calls `_ensure_user_chat_instance(target)` and resets every ChatModule in
`ctx.module_list` (`instance_id`/`instance_ids`) + `ctx.user_chat_instances[target]`
BEFORE step_5 runs — so the message lands in the thread it now belongs to, not
the original. (Rebind is best-effort/try-except: on failure the event is still
re-attributed and the message just stays in the original thread.)

## 2026-05-20 — 4.5 anchor on ANY user-visible delivery (not just human turns)

Supersedes the 2026-05-19 "仅在人-回复轮写 last_response" rule below. The session
continuity anchor must track the **last message visible in the user's chat
box** — which includes a message the agent DELIVERED to the user this turn even
from a background trigger (a scheduled job / heartbeat can call
`notify_owner`; from the user's POV that's the latest
interaction). New module-level `_turn_delivered_user_message(agent_loop_response,
working_source)` reuses the `MessageSourceRegistry` reply-tool detection (no
chat_module import — modules stay hot-pluggable, 铁律 #3). Anchor condition is
now `is_user_chat OR delivered_user_message`. For a proactive (non-human)
delivery, Step 1 skipped the anchor, so step_4 sets `current_narrative_id`
= this turn's narrative, clears `last_query`/`last_query_embedding` (no prior
user query) and sets `last_query_time=now`; `last_response` = the delivered
message. Pure machine traffic (a job/bus turn that did NOT message the user)
still leaves the anchor untouched. See [[narrative_service.py]] /
[[session_service.py]] 2026-05-20.

## 2026-05-19 — 4.5 仅在「人-回复轮」写 `last_response`

Background trigger runs (`JOB / MESSAGE_BUS / CALLBACK / SKILL_STUDY`)
不再覆盖 `session.last_response`，跟 Step 1 的 `last_query` 守护对齐。
判断口径统一走 [[hook_schema.py]] 的 `WorkingSource.is_from_human()` —
CHAT / LARK / SLACK / TELEGRAM 是 True；上面 4 个是 False。`working_source`
为 None / 未知字符串时默认按 human 处理（legacy safe default）。这是
short-reply 连续性崩 bug 修复的另一半（详见 [[narrative_service.py]]
2026-05-19 段）。

# step_4_persist_results.py — Pipeline Step 4: Persist Turn Results

## Why It Exists

After the LLM turn completes (Step 3), all results must be durably written to the database before the WebSocket connection closes. This step is the "commit point" of a turn: Trajectory records, Narrative summaries, Event status updates, Session state, and cost accounting all happen here. Steps 5–6 (hooks) run as background tasks after this step completes.

## Upstream / Downstream

**Called by:** `agent_runtime.py` — Step 4 in the 7-step pipeline

**Reads from ctx:**
- `ctx.execution_result` — the `PathExecutionResult` from Step 3
- `ctx.narrative_list`, `ctx.active_instances` — for Narrative update logic
- `ctx.event` — updated with final status
- `ctx.session` — updated with last-active timestamp

**Writes to DB (6 sub-steps):**
1. **Trajectory** — full turn record (input, output, tool calls, token usage)
2. **Markdown stats** — updates Module instance Markdown with turn statistics
3. **Event update** — marks Event as completed/failed with result summary
4. **Narrative update** — updates narrative summary and typing (default/main/auxiliary)
5. **Session** — saves updated session state
6. **Cost recording** — records LLM token costs to `agent_cost_log` table

## Key Design Decisions

### Narrative Typing Logic
Each Narrative in `ctx.narrative_list` gets typed by **list position**, not by
anything the LLM decided in Step 3:
- **main**: index 0 (post-§4.0 routing) *and* not a default Narrative — the one
  thread the turn is authored into. Only this one triggers the async LLM
  summary update.
- **default**: any Narrative with `is_special == "default"`, wherever it sits —
  it only collects the event id, nothing else.
- **auxiliary**: every other entry — a BM25 neighbour step_1 pulled in to widen
  the read-side context. It gets the event id + a `dynamic_summary` entry, no
  LLM update (`updater.py` has a standing TODO for an auxiliary-specific
  prompt).

The typing is not a persisted column — it is recomputed from list position on
every turn.

### Event Final State
The Event record (created in Step 0) is updated here with: final status (`completed`/`failed`/`cancelled`), response summary, token counts, and duration. Downstream analytics and Job scheduling depend on Event records being consistently closed.

### Cost Recording Deferred to Step 4
Although token usage is tracked throughout the turn in `ExecutionState`, the final cost record is written here (not in Step 3) because it requires the final accumulated totals from `accumulate_usage()`. Writing partial costs mid-turn would create duplicates.

### Sub-step Granularity
Each of the 6 sub-steps yields a `ProgressMessage`. This gives the frontend visibility into which persistence operation is slow (e.g., a slow Narrative embedding update), which is useful for debugging production latency.

## ContextData Mutations

Step 4 does not mutate `RunContext` fields — it reads and writes to the database. However, `ctx.event.status` is updated in-memory as a side effect (to reflect the final state before saving).

## Gotchas / Edge Cases

- **Narrative update order matters**: Narrative embedding must be updated before Markdown stats, because the embedding depends on the current narrative summary which may have just been updated.
- **Failed turns still persist**: Even if Step 3 raised an exception, Step 4 runs (in a `finally` block in `agent_runtime.py`) to record the failed Event and any partial trajectory data. Do not assume `ctx.execution_result` is always fully populated.
- **Cost recording is non-fatal**: If the cost insert fails (e.g., DB constraint), the error is logged but does not raise. A missing cost record is better than a failed turn.

## Common New-Developer Mistakes

- Adding new DB writes after Step 4 in the main pipeline: anything that needs to be durable before the WebSocket closes must go here. Steps 5–6 run as background tasks after the socket closes.
- Writing a per-Narrative COPY of the turn's `events` row to associate it with more than one thread. `narratives.event_ids` is a list — that is where the many-to-many lives. See the 2026-08-05 entry above for what copying cost us.
- Forgetting to handle the case where `ctx.execution_result` is `None` (cancelled turn) — all sub-steps must guard for this.
