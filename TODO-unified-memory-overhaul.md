# TODO：统一记忆（Unified Memory）整改 — 紧急

> 状态：**待开工 · 紧急**
> 记录日期：2026-06-05
> 来源：对 recall 工具的端到端实测（agent=本地 CC `claude-opus-4-8`，helper=`gpt-5.4-mini-2026-03-17`，测试库 `~/.nexusagent/nexusagent_tooltest.db`，user=binliang）
> 关联 todo（本地笔记，gitignored）：
> - `reference/self_notebook/todo/2026-06-03-bm25-cross-lingual-gap.md`（跨语言盲区）
> - `reference/self_notebook/todo/2026-06-05-after-hook-llm-call-sprawl.md`（after-hook LLM 调用膨胀）

---

## 0. 一句话背景

统一记忆名义上用 `remember` / `grep_memory` 跨 **7 个 `memory_<kind>` 表**（observation / chat / entity / event / narrative / job / bus）召回。
实测发现：**7 类里只有 2 类（observation、chat）是真有效的**，其余要么"迁移那天的快照之后冻结"，要么"压根没人写、永远空"。这是个**半成品的"假统一"**，要紧急整改。

---

## 1. 证据：统一记忆的真实覆盖（最重要的一张表）

| kind | 一次性迁移回填 | 日常实时写入 | 实测行数(新库) | 统一记忆能搜到? |
|---|---|---|---|---|
| **observation** | —（新概念） | ✅ 每轮 `GeneralMemoryModule.hook_after_event_execution` | 29 | ✅ 永远最新 |
| **chat** | ✅ | ✅ 每条 `ChatModule`（`chat_module.py:1235`） | 12 | ✅ 永远最新 |
| **entity** | ✅（`migrate_entities`） | ⚠️ 仅"实体被更新时" `SocialNetworkModule._feed_entity_to_engine`（`social_network_module.py:484`） | 0 | ⚠️ 半能：老的有，新认识的要等被更新 |
| **event** | ✅（`migrate_events`） | ❌ **无人写** | 0 | ⚠️ 只搜得到迁移前的；之后全瞎 |
| **narrative** | ✅（`migrate_narratives`） | ❌ **无人写** | 0 | ⚠️ 同上，冻结在迁移时刻 |
| **job** | ❌ **未迁移** | ❌ **无人写** | 0 | ❌ **永远空、永远搜不到** |
| **bus** | ❌ **未迁移** | ❌ **无人写** | 0 | ❌ **永远空、永远搜不到** |

写入点（fix 时直接看这里）：
- observation：`module/general_memory_module/general_memory_module.py:133`（`engine.retain`）
- chat：`module/chat_module/chat_module.py:1235,1248`（`repo("chat").upsert`）
- entity：`module/social_network_module/social_network_module.py:470,484`（`_feed_entity_to_engine`，只在 after-hook 更新时触发）
- event / narrative / job / bus：**全 src 搜不到任何写入点**
- 一次性迁移：`scripts/migrate_to_unified_memory.py` —— 只覆盖 entity/event/narrative/chat，**job/bus 连迁移都没做**
- 召回入口：`memory/coordinator.py`（`remember`/`grep_memory` 跨 `all_kinds()`）；7 类注册在 `memory/specs.py`

---

## 2. 实测暴露的具体问题（按严重度）

### P0 — 半统一 / 数据没人写（本文档主线）
- event/narrative/job/bus 这 4 类**日常无写入**，job/bus 连迁移都没有 → 统一记忆对它们等于失效。
- 后果实测复现：问"那个安全顾问是谁"，agent 先 `grep_memory`（统一记忆，因 `memory_entity=0` 落空）→ 再 `search_social_network`（命中）。**一件事搜两遍**，统一层成本付了、收益没拿到。

### P1 — chat 污染被动注入
- `GeneralMemoryModule.hook_data_gathering` 每轮跨类型注入 top-8（`limit=8, token_budget=800`）。
- chat 的 `RecallWeights(recency=0.8)`（`specs.py`）→ **最近对话与 query 无关也霸榜 top-8**，把真知识挤出去。
- 实测："password rotation" 查询的 top 结果竟是无关的 Alice 聊天回声；问 Alice 时 8 条里 4 条是"你的问题原文 + agent 旧回复"的回声。
- chat history 本就由 ChatModule 单独注入，统一层再 recall chat = **冗余且有害**。

### P2 — 跨语言盲区（已有独立 todo）
- BM25 纯词法：中文 query 召不回英文记忆（"密码轮换"↔"password rotation" 零交集）。被动注入和 `remember` 都中招；`grep` 要靠 agent 主动加英文词 bridge（Opus 能做到，弱模型不一定）。

### ✅ 不用改的（实测良好）
- **Agent 行为（Opus）**：上下文不够会调工具、查询失败会自我纠错、会同时试统一层+per-module、对 provenance 诚实。
- **工具 description**：把 Opus 引导得准（精确 token→grep、找人→social）。**#1 的问题不是 instruction 的错，是算法/数据的错。**
- **各工具后端 #2 检索正确性**：observation/grep/social/job/bus 后端喂对查询都能返回正确结果。

---

## 进度

- **2026-06-08 · entity 折进引擎（任务1 的一部分）✅ 已完成**
  - `SocialNetworkRepository` 重写成 `MemoryRepository("entity")` 的薄适配器；删 `_feed_entity_to_engine` 镜像；`agent_id` 仅 add 时需要（构造函数）；content_text 含 name（修了"按名字搜不到"）。
  - 连带：`agents_social_network` 路由走 repo；`auth` 账号删除按 agent_id 清 memory_* 全家族；mirror md 同步。
  - **bundle 已统一到 memory**：export/import 的 social 实体改走 repo（序列化成和以前一样的 flat 记录，content/closure/selection/id-rewrite 逻辑全保留），目的地从 `instance_social_entities` 换成 `memory_entity`。新增 `repo.save_entity()` 做完整 upsert（导入不丢 persona/related_job_ids/interaction）。`id_field_map`/scrub 的 key 从表名解耦成 `social_entities`。
  - **表保留、代码不再引用**：`instance_social_entities` TableDef 保留（Owner 决定不删，bundle roundtrip 测试用 auto_migrate 建新库；且打包信息要留存），但**代码里已无任何对该表的 DB 操作**（只剩解释性注释）。
  - 验证：功能冒烟 8/8（含 `remember("Frank")` 按名命中）；`save_entity` 完整字段保留；ruff 干净；social 6 passed；**bundle 12 passed（roundtrip 走 memory_entity）**；广测 1173 passed 零新增失败。
  - 注：其余 `memory_<kind>`（observation/chat/...）的 bundle 化是各自 kind 迁移时再做（observation 本来也没进 bundle，是历史 gap，非本次引入）。

---

## 3. 接下来要做的三条（用户 2026-06-05 指定）

### 任务 1：优化统一记忆搜索内容 —— 区分工具职责 or 合并工具
> entity 折进引擎已完成（见上「进度」）。剩余：narrative 单向投影、被动注入排除 chat/event、工具收敛。
**目标**：消除"假统一"和"双搜"。需要先定方向（二选一或混合）：
- **方向 A（做成真统一）**：让 event/narrative/job/bus/entity 都有**可靠的日常写入**（见任务 2），统一记忆名副其实，逐步退役/收敛 per-module recall 工具。
- **方向 B（明确分层）**：放弃"一个口子搜所有"的伪装，按"蒸馏知识层（observation/entity）走统一记忆" vs "结构化对象（job/bus/event/narrative）走各自的 per-module 工具"清晰切分，并在工具 description 里讲清谁管什么，避免 agent 双搜。
- **配套（无论 A/B 都要做）**：
  - 被动注入与 `remember` **排除 chat / event 这两个"对话原始层"**（解决 P1，最简单、收益直接）。
  - 评估 per-module recall 工具与统一工具的重叠，能合则合（关联 after-hook todo 里"工具表面积膨胀"那条）。

### 任务 2：解决"有些数据日常没人写"的问题
- 给 **event / narrative / job / bus**（以及补齐 entity 的实时性）**接上日常写入**：在各自的产生/更新点 `engine.retain(...)` 或 `repo(kind).upsert(...)`。
  - event：事件持久化时（`agent_runtime` Step 4 / EventService）写 `memory_event`。
  - narrative：narrative 摘要更新时（NarrativeService.update）写 `memory_narrative`。
  - job：job 创建/状态变更时（JobModule / job_service）写 `memory_job`。
  - bus：bus 消息落库时（message_bus）写 `memory_bus`。
  - entity：把"仅更新时同步"改成"创建即同步"，消除"新认识的人查不到"。
- 注意：写入要走 spec 的 dedup/consolidate，不要绕过 `engine.retain`（保持 mechanism/policy 分层）。

### 任务 3：准备 migration 脚本（之后往 dev 迁移）
- 现有 `scripts/migrate_to_unified_memory.py` **只回填 entity/event/narrative/chat**，需要：
  - **补 job / bus 的迁移**（从 `instance_jobs` / `bus_messages` 回填 `memory_job` / `memory_bus`）。
  - 设计成**幂等 + 可重跑**（dev 上可能多次跑）。
  - 若任务 2 的日常写入先上线，迁移只需补"存量历史"，新数据由日常写入覆盖——两者要对齐 record_id 规则避免重复。
  - 迁移前后打印每个 kind 的行数对账（脚本已有 `[migrate] {kind}: ... now has N rows` 的雏形，扩展到 job/bus）。
  - **dev 迁移前先在测试库验证**（`~/.nexusagent/nexusagent_tooltest.db` 或 `nexus_memrefactor.db`），确认 7 类都非空、`remember` 能跨类命中再上 dev。

---

## 4. 验收（怎么算修好了）

- [ ] 统一记忆 7 类表在跑过一段正常使用后**都有数据**（不再有"永远空"的 kind）。
- [ ] 问"某个任务/某条 agent 间消息"，`remember` **能直接命中**，不再被迫回退 per-module（或：description 明确告知该走 per-module，agent 不再双搜）。
- [ ] 被动注入的 top-8 里**不再出现 chat 回声**挤占真知识。
- [ ] migration 脚本幂等、覆盖全 7 类、有行数对账，先测试库验证再上 dev。
- [ ] 复跑本次的探针（Frank 安全顾问 / vendor 对账 job / 跨类问任务）：agent 一次命中、不双搜。

---

## 5. 复现/继续测试的环境备忘

- 测试库：`~/.nexusagent/nexusagent_tooltest.db`（WAL 模式，已造 ~29 observation + 4 social 实体 + 3 job + 2 bus agent）。
- LLM 槽（binliang）：agent=`claude-opus-4-8`（本地 CC OAuth，`prov_cc_oauth`），helper=`gpt-5.4-mini-2026-03-17`（真 OpenAI，`prov_openai_real`）；两槽 `last_auto_repaired_at` 设到 2099 锁住防 self-heal 改模型。
- MCP servers：`make dev-mcp`（DATABASE_URL 指向测试库），端口 7802/7803/7804/7808/7809/7820；**GeminiRag 已删，7805 空**。
- 交互脚手架：`/tmp/tooltest/harness.py`（跑一轮 + 从 event_log 提 tool 调用与结果）。

---

## 6. 2026-06-08 进展 + 明天继续

### 今日完成
- **Parity 收尾**：`crud._index_narrative` 的可搜文本对齐 narrative 路由字段（name + current_summary + **description** + topic_keywords），`remember` 与 turn-routing 共用同一 `bm25_rank`、搜同一组字段。ruff 全绿，26 文件待 commit（P0–P3 + parity）。
- **Lark 文档**：《NarraNexus Agent · 数据全生命周期》已建（docx/YU37ddokpoTadVx8NuelhJfugGb）。

### E2E 测试结果（确定性探针，硬证据）
> 脚本 `/tmp/tooltest/e2e_seed_probe.py`；库=binliang 的 dev 库 `~/.nexusagent/nexusagent.db`（**非生产** `~/.narranexus/nexus.db`）。3 条主题线 + 6 类数据全走真实写入路径。

- ✅ **remember 跨 6 类召回**：问"Apex 380万差额"→ 命中 bus/entity/event/job/narrative/observation 全 6 类，每条带 `source_ref` 指针。重构前 job/bus/narrative/interaction 全空，这是核心赢点。
- ✅ **narrative BM25 路由 3/3 准**：财务 0.886 / 户外 0.903 / 技术 0.844，置信高。
- ✅ **化学反应机制成立**：`remember(kinds=narrative)` 返回 `{kind:narrative,id:nar_e2e_finance}` —— 正是 `switch_narrative` 所需指针。
- ⚠️ **发现 RRF 缺陷（新待办）**：记录数少的 kind（event/bus/job 各仅 1 条且都财务）其 top-1 被 RRF 无脑塞进结果，户外 query 也捞回财务条目、甚至压过正确户外 narrative。`engine.recall()` 缺**绝对 BM25 分相关性门槛**。生产中每类多记录会缓解，但需修：低分候选不进 RRF（per-kind 加最小分阈值或 RRF 前按分裁剪）。

### 活体 loop 进展（未完成，明天继续）
- 服务启动：因机器上 **hongyi.gu 也跑一套 NarraNexus**（占 8100 proxy），改为**直接后台拉起**各服务（不用 tmux）；我的服务全直连 `nexusagent.db`（`SQLITE_PROXY_URL` 未设→direct sqlite，无交叉污染）。8100 proxy 失败无害。
- **关键约束**：agent 槽**必须用 anthropic 协议的 provider**（`prov_nx_test`），openai 行无法服务 agent 槽（报 `LLMConfigNotConfigured: NetMindDriver on protocol='openai' cannot serve the agent slot`）。
- ✅ DeepSeek-V4-Pro **会主动调** `remember`(×3)/`grep_memory`/`view_narrative`（turn1 即调，还查看了 finance+tech 两条线）。工具调用嵌在 `agent_response` 事件的 `tool_name` 字段（非独立 `tool_call` type）。
- ⚠️ **测试 setup 缺陷**：直接 seed 的 narrative **没有关联 event/chat instance**，agent final 回 "这个 narrative 的记录似乎没有完整加载出来"。要验证 switch_narrative 化学反应，明天需：
  - **改 seed**：给 narrative 建真实 event 关联 + chat instance（或走多轮真实交互自然积累）；
  - 再跑 **turn2**（session 锚定 tech 线 → 问财务）观察 agent 是否 `remember`→`switch_narrative` 跳线。

### 明天继续（优先级）
1. ✅ **修 RRF 相关性门槛**（commit `ddd4078`）—— 两层修复：① `rank_recall` 非空 query 只排相关命中（recency 不再复活零相关项）；② tokenizer 加保守 CJK 停用词（功能字 的/这/个/是… 不再制造虚假重叠）。新增 `tests/memory/test_recall_relevance_gating.py`（7 例）。实库复验：户外 query 不再泄漏财务 event/bus/job；路由仍 3/3、分数不变。
2. ✅ **活体 loop 化学反应验证完成**（2026-06-09，agent `agent_b43a44ef8692`，4 轮真实对话，DeepSeek-V4-Pro）。结论比假设更强：
   - **t1 财务** → 自然建 finance 线 `nar_9ca2`；**t2 户外** → agent 主动 `create_narrative` 建户外线 `nar_a7bc`，session 锚户外。
   - **t3（关键）**：锚在户外线时问财务 → step-1 路由**真的粘在户外线**（mis-route 成立）；agent 调 `remember` **跨线召回财务内容**（张伟×23/Apex×16/380×15/对账/预付款），**就地正确作答**（任务+具体事项+状态全对），**未** `switch_narrative`。
   - **t4（显式恢复）**："回到 Apex 对账线" → BM25 路由**正确切回 finance 线 `nar_9ca2`**；agent 自述"已在正确线、无需切"，并 `job_create` 设周五提醒。
   - **效果结论**：用户假设的"mis-route → 搜出 narrative → switch_narrative 跳线"在实践中走了**更优路径** —— 化学反应由 **remember 跨线召回内容 + 强 BM25 路由** 共同完成，`switch_narrative`（工具确实可用、basic_info_module）**未被需要**（agent 显式推理判断不必切）。**"会对效果有提升"= 明确 YES**：agent 坐在错的 narrative 线上仍能准确回答跨线问题。
   - 残留：t3 路由粘连本身（"对了，之前…"被判为连续）是否理想可再议，但不影响答案正确性（remember 兜住）。
3. ✅ **mirror md 同步完成**（2026-06-08）：两次 commit（`4cd9867`+`ddd4078`）涉及的 21 个源文件 mirror 全部对齐 `last_verified: 2026-06-08`（18 个加日期注记 + 新建 `crud.py.md` + 昨天已更的 social_network 两个；`memory/__init__.py` 纯 re-export 不建 mirror）。
4. **任务3 迁移**（存量回填）仍未动。

### 测试副作用（明天决定是否还原）
- **binliang LLM 槽被改**（DB，`nexusagent.db`）：
  - `agent` → `prov_nx_test` / `deepseek-ai/DeepSeek-V4-Pro`（原值 `prov_nx_test` / `minimax/minimax-m2.5`）
  - `helper_llm` → `prov_nx_test_openai` / `deepseek-ai/DeepSeek-V4-Pro`（原值 `prov_nx_test_openai` / `deepseek-ai/DeepSeek-V3`）
- **provider models 列表加了 V4-Pro**：`prov_nx_test`、`prov_nx_test_openai`。
- **测试 agent**：`agent_ca9b6c3390c7`（"记忆E2E测试机"，已 provision）、`agent_e2emem01`（未 provision，仅 seed 数据）。
- **seed 数据**（两 agent 名下）：3 narratives `nar_e2e_{finance,outdoor,tech}` + observation/entity/job/bus/event 索引。
- **探针脚本**：`/tmp/tooltest/e2e_seed_probe.py`、`/tmp/tooltest/ws_drive.py`、事件 `ws_events_turn1.jsonl`。
- **后台服务**：今日已起（backend:8000 + mcp 78xx + poller + job/bus trigger），收尾时停掉；重启用 §"复现"那套 nohup 直接拉起（不用 tmux，避开 hongyi.gu 的 8100）。
