---
code_file: src/xyz_agent_context/module/chat_module/chat_module.py
last_verified: 2026-05-20
---

## 2026-05-20 — conversation write moved to synchronous `hook_persist_turn`

The conversation-row write (build user+assistant messages → `add_instance_json_format_memory`)
moved OUT of `hook_after_event_execution` into the new SYNCHRONOUS `hook_persist_turn`
(see [[base.py]] / [[hook_manager.py]] / [[agent_runtime.py]] Step 4.6). Reason: the
old write lived in the backgrounded hook, which lags 3–19s; a user replying instantly
raced it and the next turn read history missing the exchange ("short-reply amnesia").
`hook_persist_turn` now writes it in-request, before the WS closes. What stays in the
background `hook_after_event_execution` is ONLY the heavy Part-B embedding
(`_embed_message_pair`), which re-locates this turn's user+assistant pair by `event_id`
(robust to a later turn appending more messages before the background task runs).

## 2026-05-20 (Fix #2 hotfix) — resolve cross-narrative tag via links table

P1 originally read each cross-narrative chat instance's narrative off
`instance.linked_narrative_ids[0]`. That was wrong and crashed in prod:
`InstanceRepository.get_chat_instances_by_user` returns BASE
`ModuleInstanceRecord` objects, which have NO `linked_narrative_ids` attribute
(that field lives only on the `ModuleInstance` subclass in [[instance_schema.py]]
and isn't populated by the SELECT anyway). The access raised `AttributeError`,
the whole short-term load was swallowed by the try/except → the agent saw zero
cross-narrative history → "amnesia". New
`_resolve_instance_narratives(instance_ids)` maps each instance_id → narrative_id
via `InstanceNarrativeLinkRepository.get_narratives_for_instance`
(`instance_narrative_links`), used by BOTH `_load_short_term_memory` and
`_load_recent_actions`. The unit tests now build REAL base records (no attr) +
mock the link repo, so they fail the way prod did if anyone reads the attribute
off the record again. (Below, "the source instance's `linked_narrative_ids[0]`"
is superseded by this resolution.)

## 2026-05-20 (Fix #2 P1) — unified time-sorted chat history, tagged by narrative

`hook_data_gathering` no longer produces a long-term list + a separate
cross-narrative blob. It now builds ONE timeline: the current narrative loaded
in FULL (the old 40-cap removed) + cross-narrative via `_load_short_term_memory`,
merged by timestamp, capped at `MERGED_HISTORY_MAX` (30, latest by time). Every
message is tagged `meta_data.narrative_id` (long-term = `ctx_data.narrative_id`;
cross = the source instance's `linked_narrative_ids[0]`) and
`_tag_narrative_aliases()` batch-resolves each id → narrative name for the
`[time · topic · nar_id]` tag the agent sees. `_load_short_term_memory` is now
PURE RECENCY (latest `SHORT_TERM_MAX_MESSAGES`=30 by time; the 2026-05-11
per-instance fairness cap / `SHORT_TERM_PER_INSTANCE` removed — Owner's call).
The reasoning-splice (carry-forward of device codes / job ids / URLs) is KEPT.
Heavy `[ChatHistory]` logging makes assembly verifiable from logs alone. Each
timeline tag now also carries `evt=<event_id>` so the agent can drill into a
turn's full agent-loop/reasoning via view_event; `[ChatHistory] timeline
event_ids` logs the loaded ids for debugging (no raw text). See
[[context_runtime.py]] for rendering + the preamble.

## 2026-05-20 (Fix #2 P2) — recent background-activity track

`_load_recent_actions()` collects the latest `RECENT_ACTIONS_MAX` (10)
`message_type='activity'` rows across the user's chat instances — the centered
small-text items in the UI (job runs, IM/channel activations, bus pings the
agent did WITHOUT replying). These are still filtered OUT of the conversation
timeline; surfaced separately (stored on `ctx_data.extra_data['recent_actions']`)
with each row's `event_id` (for view_event drill-down) and a best-effort job
title pulled from the event's env_context. [[context_runtime.py]] renders them.

## 2026-05-12 P0 #3 followup — drop final_output fallback, defer to step_3 helper_llm

Reverted the 2026-05-11 "use io_data.final_output as reply content"
fallback. That fallback violated the project's thinking-vs-speaking
design (final_output is the agent's internal reasoning, not a
user-facing reply) and could persist meta-talk like "Let me check the
chat history first" as the assistant's spoken line, then poison the
next turn's context. The 5/11 product review with Xiong explicitly
ruled out this shortcut.

The real no-reply recovery now lives one layer up in
`step_3_agent_loop._generate_fallback_reply_stream`: when a chat-
trigger turn ends without `send_message_to_user_directly`, step 3
calls helper_llm with the agent's reasoning as background, streams a
user-facing reply through `AgentTextDelta` (frontend renders it like
any other agent reply), and finally emits a synthetic
`send_message_to_user_directly` ProgressMessage carrying
`details.reply_via="helper_llm_fallback"`.

`hook_after_event_execution` is now a pure consumer:
- the synthetic ProgressMessage flows through
  `_extract_user_visible_response` like any organic send_message call,
  so `assistant_content` is the helper_llm reply text (not reasoning).
- a tiny scan of `agent_loop_response` lifts
  `details.reply_via="helper_llm_fallback"` onto the persisted row's
  `meta_data.reply_via` field so observability tooling can distinguish
  organic vs. recovered replies.
- if step 3's helper_llm fallback failed too, the row still carries
  `(Agent decided no response needed)` placeholder — that's the honest
  record, not a silent backfill of reasoning.

Pinned by `tests/chat_module/test_error_severity_and_fallback.py`:
- `test_helper_llm_fallback_marker_is_propagated`
- `test_no_reply_tool_and_no_helper_llm_fallback_persists_placeholder`

## 2026-05-11 P0 #3 — error detail, no-reply differentiation, final_output fallback

Three changes addressing the "Agent decided no response needed"
recurring P0 (Lark recviIcuKMNuHj / Xiong's 60% failure rate):

1. **`_detect_error_in_agent_loop` → `_detect_fatal_error_in_agent_loop`**.
   Only `ErrorMessage(severity="fatal")` collapses the turn into a
   failed user-only row. Recoverable signals (mid-loop rate-limit
   blips emitted by ResponseProcessor) keep the turn alive so the
   agent can react and still produce a reply. The old name is kept as
   an alias for backwards compat with existing tests.

2. **Failed-turn rows persist `error_message`, not just `error_type`**.
   `_FAILED_TURN_ANNOTATION_TEMPLATE` now substitutes the actual
   error message into the next-turn annotation, so when the LLM (or
   an operator) reads `[Previous turn failed... Error type: X.
   Detail: Y. Do NOT retry]`, it sees *why* — ops no longer need to
   grep stderr to learn what happened.

3. **`final_output` fallback** (Bug B fix). When
   `_extract_user_visible_response` returns the placeholder but
   `io_data.final_output` is non-empty, we persist `final_output` as
   the assistant content and tag the row with
   `meta_data.reply_via="final_output_fallback"`. Pre-fix, the agent
   would stream LLM-native output to the user (visible mid-turn) but
   the persisted row was just `(Agent decided no response needed)` —
   the next turn's prompt then showed the agent saying it decided not
   to reply, training the model into a self-reinforcing failure loop.
   Production data (RDS, 2026-05-11): chat-trigger placeholders had
   `events.final_output` non-empty in 83/90 cases (92%) — those are
   the rows the fallback will recover.

New `[NO-REPLY]` / `[NO-REPLY-BG]` / `[TURN-FAILED]` / `[FALLBACK]`
WARNING-level log markers fire on each path so ops can grep production
logs and instantly see *which* path a turn ended on and why.

Pinned by `tests/chat_module/test_error_severity_and_fallback.py`.

## 2026-05-11 follow-ups — recency cap + short-term fairness

After landing the per-source dispatch fix, three knobs in this file
got tuned in the same direction (better recall of meaningful history):

- `MAX_RECENT_MESSAGES`: **30 → 40** (chat_module.py around line 432).
  Long narratives were hitting the old cap and silently losing the
  earlier half of the conversation. 40 is still count-based — a
  token-based cap is the right next step if 40 starts to matter.
- `SHORT_TERM_PER_INSTANCE = 5` new constant. `_load_short_term_memory`
  now runs **two stages**: Stage A caps each cross-narrative
  ChatModule instance at its 5 most recent rows; Stage B merges and
  applies the existing `SHORT_TERM_MAX_MESSAGES = 15` global cap.
  Pre-fix, one chatty instance could fill all 15 slots and starve
  every other narrative the user had touched. Pinned by
  `tests/chat_module/test_short_term_fairness.py`.

Both changes are read-side only — no schema, no migration.



`hook_after_event_execution` used to stamp the injected
`BOOTSTRAP_GREETING` row with `utc_now()` (the moment the hook runs,
i.e. after the agent loop finishes), while the user's first message
carries `event.created_at` (turn-start). Because the agent loop spans
seconds to minutes, the greeting timestamp ended up *later* than the
user message timestamp. Both the chat-history API
(`backend/routes/agents_chat_history.py`, sorts by
`meta_data.timestamp` ascending) and the frontend timeline
(`frontend/src/components/chat/ChatPanel.tsx`, also ascending sort)
then rendered the greeting *under* the user's first query bubble —
the P0 "agent主动问好的消息跑到 query 底下了" filed by Xinyao.

Fix: anchor the greeting at `event.created_at - 1ms` (or
`utc_now() - 1ms` as defensive fallback when `params.event` is None),
keeping the persisted ordering greeting → user → assistant under any
timestamp-ascending sort. Regression pinned in
`tests/chat_module/test_bootstrap_greeting_order.py`.

The frontend never needed changing: the in-session greeting injection
in `ChatPanel.tsx` already stamps `Date.now() - 1`, which dedups
correctly against the (now earlier) DB greeting via the role+content
key inside the 5-minute SAME_MESSAGE_WINDOW.

## 2026-04-28 changes — half-finished features parked

Two writer paths in `hook_after_event_execution` were exercising
features whose reader half was never built. Cleaned up to stop the
ongoing waste and the noise floor they were creating.

**Part B embeddings (`_embed_message_pair`)** — now actually works.
The `chat_message_embeddings` table is no longer missing from the
schema (it was the only legacy "one create script per table" leftover;
`schema_registry.py` now owns it). Each turn writes one
`(user, assistant)` embedded pair as before, and the writes finally
land. No reader yet — when Part B retrieval is built, it'll find a
populated table to query against. **Cost note:** every turn still
spends one embedding API call on data nobody reads yet. If embeddings
are expensive enough to matter before the reader lands, switch this
back off — but on a per-turn basis the cost is small (~one `get_embedding`
call) so we left it on as future-data investment.

**ChatModule status report (`update_report_memory`)** — disabled.
The block that built a one-line "Conversation rounds: N | Latest …"
report and called `event_memory_module.update_report_memory(...)` is
commented out (in place, with explanation) inside
`hook_after_event_execution`. Two reasons stacked:
  1. The reader half (`get_report_memory`) has zero callers anywhere
     — no Narrative orchestration code consumes the reports.
  2. The writer was failing in production anyway because the live
     `module_report_memory` table still has a legacy
     `instance_id NOT NULL` column from an older schema, and the
     current INSERT (narrative_id / module_name / report_memory) does
     not fill it. After T12's `error → exception` sweep the failure
     started printing a full SQLite stack to logs every turn, which
     is what surfaced the bug.

The block stays as commented code (not deleted) so reviving the
feature is a one-block-toggle once a `get_report_memory` consumer
lands. Don't uncomment without first reconciling the
`module_report_memory` schema — see
`.mindflow/mirror/.../event_memory_module.py.md`.

## 2026-04-23 update — 持久化 Agent reasoning 以跨 turn

`hook_after_event_execution` 现在除了保存 `send_message_to_user_directly` 的 content（用户可见文字），还把 `params.io_data.final_output`（Agent 的 reasoning）**完整**存到 assistant 消息的 `meta_data.reasoning`。曾考虑过加长度 cap，决定**不截断**——reasoning 是 Agent 自己写的（自然自限长），而且截断会冒风险切掉正是 Agent 要跨轮保留的那个长串（device_code、file token）。

`hook_data_gathering` 在所有 load + sort 完成后，遍历 `all_messages`：对每条 assistant 消息，如果 `meta_data.reasoning` 非空，把 content 包成：
```
<my_reasoning>
{reasoning}
</my_reasoning>

<reply_to_user>
{original content}
</reply_to_user>
```

**动机**（2026-04-23 产线事件，agent_7f357515e25a）：增量 Lark scope 授权时，`auth login --no-wait` 返回的 `device_code` 值只在那一轮的 `tool_call_output_item` 里，不跨 turn。Agent 下一轮想用 `--device-code <D>` poll 时拿不到 `D`，只能写出 `auth login --device-code --as ...`（缺值），回退到 `--no-wait` 重铸——orphan 用户点过的 URL。本修改让 Agent 可以通过在 reasoning 里 restate 关键值（device_code、job_id、token 等）把它们带到下一轮。前端 chat_history API 拿到的 row 不变（content 字段还是 send_message 原文），splicing 只发生在**喂 LLM 之前的那一次渲染**；持久化的 row 只是多了 `meta_data.reasoning` 字段。

配套变更：
- `src/xyz_agent_context/module/basic_info_module/prompts.py` 新增 "Working Memory Across Turns" 段，向所有 trigger 源的 Agent 说明"tool output 一次性，reasoning 跨轮"这件事 + 要求 Agent 主动 restate 关键值到 reasoning
- `src/xyz_agent_context/module/lark_module/lark_module.py::_INCREMENTAL_AUTH_GUIDE` 追加一条 bullet，明确说 mint 完后要把 device_code/scope/URL 写进 reasoning
- 回归 pin 在 `tests/chat_module/test_reasoning_persistence.py`（持久化 + splicing 双向）、`tests/basic_info_module/test_cross_turn_memory_guidance.py`（prompt 三句话）、`tests/lark_module/test_incremental_auth_guide.py::test_guide_reminds_agent_to_restate_device_code_in_reasoning`

**不改前端** — frontend 的 chat bubble 照旧读 `get_simple_chat_history` 返回的 content，看到的还是 send_message 原文。meta_data.reasoning 仅供后端组装 LLM 上下文用。

# chat_module.py — ChatModule 实现

## 为什么存在

ChatModule 解决两个核心问题：让 Agent 在对话中访问过去的交流历史，以及在对话结束后把这轮对话持久化。它同时定义了"用户可见响应"的提取逻辑——只有通过 `send_message_to_user_directly` 工具发送的内容才算用户可见，Agent 的内部推理过程不记录为 assistant 消息。

**Hook 实现**：同时实现了 `hook_data_gathering`（双轨记忆加载）和 `hook_after_event_execution`（对话持久化）。

**MCP 端口**：7804

**Instance 模型**：Narrative 级别，每个 Narrative 里每个用户有独立的 Chat 实例（`instance_id` 格式：`chat_xxxxxxxx`）。

## 上下游关系

- **被谁用**：`ModuleLoader` 自动加载；`HookManager` 调用两个 hook；`ModuleRunner` 启动 MCP
- **依赖谁**：`EventMemoryModule`（存储后端）；`InstanceRepository`（短期记忆时查找其他 Chat 实例）；`_chat_mcp_tools.py`（MCP 工具实际定义）；`bootstrap/template.BOOTSTRAP_GREETING`（首次对话时注入问候语）

## 设计决策

**双轨记忆的优先级**：EverMemOS 语义记忆（`ctx_data.extra_data["evermemos_memories"]`）优先于 DB 事件记忆。如果 EverMemOS 没有数据（新 Narrative、EverMemOS 不可用），则 fallback 到直接从 `EventMemoryModule` 读取历史。EverMemOS 路径不依赖 `EventMemoryModule`，是更高质量的语义压缩记忆。

**短期记忆移除了时间窗口限制**（2026-02-09 优化）：早期版本限制 30 分钟内的跨 Narrative 消息，但这导致非活跃用户的短期记忆总是空。改为直接取最近 15 条（`SHORT_TERM_MAX_MESSAGES = 15`），不论时间。

**背景任务的 activity record 而非 fake 对话**：当 `working_source != "chat"` 且 Agent 没有调用 `send_message_to_user_directly` 时，不记录一对 user/assistant 消息，而是记录一条 `message_type: "activity"` 的简短描述（如 "Executed a background job"）。防止历史记录被无意义的 "(Agent decided no response needed)" 污染。

**失败轮隔离（Bug 8）**：当 agent loop 抛错时，`_detect_error_in_agent_loop` 从 `params.agent_loop_response` 扫出 `ErrorMessage`（`step_3_agent_loop.py` 在 catch Exception 分支里把 ErrorMessage 既 yield 也 append，保证下游 hook 看得到），`hook_after_event_execution` 只存 user 消息，`meta_data` 里打 `status="failed"` + `error_type=...`，**不写任何 assistant 行**（partial 输出也丢）。下一轮 `hook_data_gathering` + `_load_short_term_memory` 都会过 `_apply_failed_turn_filter`：失败的 user 行被重写成"Previous turn failed... Do NOT retry"的注解（保留原问题文本，方便代词解析），遗留的失败 assistant 行被丢。目的是让 LLM 看到"那轮断了"而不是"那轮我只说了一半还没说完"——后者正是污染下轮 prompt 让 LLM 重复执行上轮查询的根因。

**MCP 工具逻辑抽取到 `_chat_mcp_tools.py`**：2026-03-06 拆分，保持 `chat_module.py` 专注于 Hook 生命周期，MCP 工具注册逻辑独立维护。

## Gotcha / 边界情况

- **Bootstrap greeting 注入**：如果 `ctx_data.bootstrap_active=True` 且是第一轮对话（历史为空），会在写入历史前先插入一条 BOOTSTRAP_GREETING 作为第一条 assistant 消息。这是一次性逻辑，仅发生在 Agent 第一次被激活时。
- **`channel_tag` 的传递**：`hook_after_event_execution` 里从 `ctx_data.extra_data["channel_tag"]` 读取渠道信息（Matrix 房间、发送者等）并写入每条消息的 `meta_data`。如果 `channel_tag` 是 Pydantic 对象（而非 dict），会调用 `.to_dict()` 转换。忘记这个转换会导致 JSON 序列化失败。

## 新人易踩的坑

- 误以为 `instance_id` 就是用户 ID——`chat_xxxxxxxx` 是 Module 实例的 ID，不是用户 ID。一个用户在不同 Narrative 里有不同的 Chat 实例。`get_chat_history` 工具需要的是 `instance_id`，不是 `user_id`。
- 调试时看到 `chat_history` 为空但数据库里有记录——通常是 `instance_id` 不对导致的：ModuleLoader 注入的 `instance_ids` 不包含要查的实例。
