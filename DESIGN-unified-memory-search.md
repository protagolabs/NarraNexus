# 设计：统一记忆 · 搜索层（Unified Memory — Search Layer）

> 状态：**P0–P3 已实现 + 验证（2026-06-08）**；E2E 待跑
> 进度：P0 地基 ✅ · P1 narrative 投影 ✅ · P2 chat/event 合并 interaction 索引 ✅ · P3 job/bus 投影 ✅
> 每期：功能冒烟（source_ref 命中）+ 回归全绿（job/bus/memory/narrative/bundle/chat 合计 80+ passed）+ 全量 ruff 干净。
> 实现位置：`source_ref`=record.py/schema_registry · `index()`=engine.py · 两清单=spec.passive/passive_kinds + general_memory hook · narrative=crud._index_narrative · interaction=step_4 · job=job_repository.create_job · bus=local_bus.send_message · chat 镜像删除+chat spec 退役。
> 仍待（任务3/迁移）：存量回填（entity 新 record_id 重灌、job/bus 首次建索引、interaction 从 events 回填）、bundle 处理新 memory_* 、bus 接收方 fan-out（接收方召回目前由 interaction 索引覆盖）。
> 日期：2026-06-08
> 关联：`TODO-unified-memory-overhaul.md`（这是其「任务1剩余 + 任务2」的正式设计）
> 评审决策（Owner 2026-06-08）：①显式三层抽象 ②chat/event 合并成"一次交互一条索引" ③job+bus 都进搜索（索引+指针指向实时）

---

## 0. 出发点（Owner 的核心模型）

这些数据有**两种使用属性**：
1. **作为上下文信息源**注入，驱动 agent 的动作；
2. **作为可搜索信息**，agent 能搜到、并拿回原始数据。

→ 这就是把它们当 "memory-like" 的原因：**把可搜索的部分提取出来、做好关联，搜到后能取回原始数据**。

不同数据，**家不同、搜法不同**（chat 按时间直接拿、job 按描述关键词检 + 按 id 直取……）。当前实现把这些角色糊在一起、各 kind 各做各的，没有统一抽象——这就是"假统一"的根。

---

## 1. 核心模型：**三层**（Inject / Search / Fetch）

把"两种属性"落到实现，拆成**三个动作**，因为"搜索"和"取回原始"是两步：

```
┌──────────────────────────────────────────────────────────────┐
│ ① Inject（注入）  每轮自动把"驱动动作要用的"塞进 ctx_data       │
│    谁做：各模块的 hook_data_gathering（各模块最懂自己）          │
│    例：chat 塞最近对话、job 塞活跃任务、social 塞当前人、        │
│        general_memory 塞 top-N 蒸馏知识                          │
├──────────────────────────────────────────────────────────────┤
│ ② Search（搜索）  按内容/关键词【找到】相关的东西，返回【指针】  │
│    谁做：统一的 memory engine（remember / grep_memory）          │
│    跨 kind，返回 hit = { content_snippet, source_ref, score }    │
├──────────────────────────────────────────────────────────────┤
│ ③ Fetch（取原始）  拿指针去源表取回【实时】原始数据             │
│    谁做：各模块的 by-id / by-time 工具                          │
│    例：view_event / view_narrative / get_chat_history /          │
│        job_retrieval_by_id / get_contact_info                   │
└──────────────────────────────────────────────────────────────┘
```

**关键纪律**：三层各司其职、互不混淆。Search 只负责"找到 + 给指针"，**不负责存全量、不负责出实时状态**；实时状态永远从源表 Fetch。

---

## 2. 统一抽象：**memory_<kind> = 搜索索引**（可搜文本 + 指针 + 排序信号）

`memory_<kind>` 不再被理解为"数据的另一个家"，而是**源数据的搜索索引**。每条索引记录 = 三样东西：

| 部分 | 字段 | 作用 |
|---|---|---|
| **可搜文本** | `content_text` | BM25 / grep 匹配的目标（源数据里"可被搜索的提取"，不一定是全量） |
| **指回原始的指针** | `source_ref`（**新增**） | `{kind, id}`——搜到后据此去源表取实时原始；自包含型为空 |
| **排序信号** | `tags / proof_count / salience / created_at / last_used_at` | 每 kind 的 `RecallWeights` 微调 |

### 两种记录原型

- **自包含型（self-contained）**：`observation` / `entity`。索引记录**就是**数据，源 = 引擎本身，`source_ref` 为空，搜到直接拿。
- **索引/投影型（reference）**：`event(=interaction)` / `narrative` / `job` / `bus`。索引记录 = 可搜文本 copy + `source_ref` 指针，**源在操作表**。搜到 → 按指针 Fetch 取实时原始。

> 这彻底消灭"镜像病"：投影型记录是**只读派生 + 单一写入点**，且明确"我只是索引、不是真身"；操作型数据的实时状态永远在源表，索引绝不冒充。

---

## 3. Schema 变更（additive，铁律#6）

`MemoryRecord` + `memory_<kind>` 表新增：

```python
# memory/record.py — MemoryRecord
source_ref: Optional[Dict[str, str]] = None   # {"kind": "...", "id": "..."} 指回源记录；自包含型为 None
```
- 表层：`memory_<kind>` 加 `source_ref TEXT/JSON`（schema_registry 的 `_memory_kind_table` 统一加，additive）。
- `source_ids`（已有，provenance "由哪些 event 产生"）与 `source_ref`（"我索引的那条原始记录"）**语义不同，并存**。

---

## 4. 逐 kind 方案

| kind | 原型 | 源表（Fetch 实时） | 可搜文本（content_text） | source_ref 指针 | 进②搜索? | 进①被动注入? |
|---|---|---|---|---|---|---|
| **observation** | 自包含 | 引擎 | 事实句 | — | ✅ | ✅ |
| **entity** | 自包含 | 引擎（已折入） | name+aliases+desc+keywords | — | ✅ | ✅ |
| **narrative** | 投影 | `narratives` | 摘要 + 标题 | `{narrative, id}` | ✅ | ✅ |
| **interaction**（= 合并的 chat/event） | 投影 | `events`（完整）/ chat 表（对话片段） | user 输入 + agent final_output（+ 关键工具结果摘要） | `{event, id}` | ✅ | ❌（最近对话已由 ChatModule 单独注入） |
| **job** | 投影 | `instance_jobs`（实时状态） | title + description | `{job, id}` | ✅ | ❌（活跃 job 已由 JobModule 注入） |
| **bus** | 投影 | `bus_messages` | 消息内容 | `{bus, id}` | ✅ | ❌（未读已单独注入） |

**两个召回面用不同 kind 清单**（`coordinator.remember(kinds=...)` 本就支持）：
- **被动注入**（hook_data_gathering，每轮 top-N）：`observation + entity + narrative`（蒸馏知识）。
- **remember/grep 工具**（agent 显式调）：全部 6 类（含 interaction/job/bus）——"回忆我们聊过什么 / 我做过哪个任务 / 某 agent 跟我说过什么"。

---

## 5. chat / event 合并（Owner 决策②）

- **辨析**：`event` = 一次 agent-loop turn 的完整原始记录（trigger→thinking→工具→final_output）；`chat` = 对话文本，是 event 的"对话投影"。**chat ⊂ event**。
- **合并**：**一次交互一条索引**（event 粒度）写入 `memory_event`：
  - `content_text` = 本轮 user 输入 + agent final_output（对话内容，可搜）；
  - `source_ref = {event, event_id}`。
- **指针取不同粒度的原始**：命中后 → `view_event(id)` 取完整 trace；或 `get_chat_history` 取该实例的对话上下文（按时间）。
- **`memory_chat` 退役**：ChatModule 的 `memory_chat` 镜像写入（`chat_module.py:1186-1235`）删除，其搜索职责并入 interaction 索引。`get_chat_history` 仍读操作表 `instance_json_format_memory_chat`（Fetch/Inject 层不变）。P1 chat 污染**顺带根治**（chat 不再进被动注入、且 interaction 索引按相关性而非 recency 排序）。

---

## 6. 投影写入点（②的"日常写入"——其实是"建索引"）

每个源在**产生/更新**时，提取可搜文本 + 指针，写一条索引（走统一接口，单一写入点）：

| kind | 写入点 |
|---|---|
| observation | `GeneralMemoryModule.hook_after_event_execution`（已有） |
| entity | `SocialNetworkRepository`（已有，已折入引擎） |
| **narrative** | `NarrativeService.update_narrative`（摘要更新时）——**先确认是唯一写入点**，避免 entity 那种"多路径漏一条" |
| **interaction** | `step_4_persist_results`（event 落库时，一次交互一条） |
| **job** | job 创建/更新（`job_service` / JobModule）——只索引 title+description+指针，状态不进索引 |
| **bus** | bus 消息落库（`message_bus`） |

> 统一接口建议：engine 上加 `index(source_kind, source_id, text, *, scope, tags)`，内部 upsert 一条投影记录（确定性 record_id = `idx_<kind>_<hash(source_id)>`，幂等）。投影型都走它，杜绝各写各的。

---

## 7. 搜索返回结构（Search → 给指针）

`remember` / `grep_memory` 的每个 hit 暴露给 agent：
```json
{ "kind": "job", "memory": "<可搜文本片段>", "when": "...", "source": {"kind":"job","id":"job_abc"} }
```
- agent 看到片段 + 知道"原始在哪"→ 需要详情/实时状态时，用 `source.kind` 对应的 by-id 工具 Fetch。
- 自包含型（observation/entity）`source` 为空 = "片段即全部"。
- 工具 description 要讲清这个"搜到→按 source 取原始"的两步法（让 agent 学会 Fetch）。

---

## 8. 实现分期（建议）

> 每期独立可验证、可回滚。每期复用前面的统一 `index()` 接口。

1. **P0 · 地基**：`MemoryRecord.source_ref` + schema 列 + engine `index()` 接口 + `coordinator.remember(kinds=...)` 两清单（被动注入 vs 工具）。
2. **P1 · narrative 投影**：`update_narrative` 建索引（自包含→投影的第一个范例）。
3. **P2 · interaction 合并**：`step_4` 建 interaction 索引；删 `memory_chat` 镜像；被动注入排除 chat/event（根治 P1 污染）。
4. **P3 · job / bus 投影**：各自写入点建索引；搜索命中按 source_ref 指向实时。
5. **P4 · 迁移（= TODO 任务3）**：存量回填（含 entity 用新 record_id 重灌、job/bus 首次建索引）、bundle 统一处理、`instance_social_entities` 等旧表按需删。

---

## 9. 验收（怎么算这层做成了）

- [ ] `remember("我之前转错账那个对账的活儿")` → 命中 job 索引 → 按 source_ref 取到**实时** job 状态（不是过期快照）。
- [ ] `remember("三周前聊的并购")` → 命中 interaction 索引 → `view_event` 取回完整原始。
- [ ] 被动注入 top-N 里**不再有 chat 回声**；observation/entity/narrative 占满。
- [ ] 每个投影 kind 只有**一个写入点**；新增源数据立即可被 remember 搜到（无"冻结快照"）。
- [ ] 7 张 memory_* 表无"永远空"的；job/bus 有索引。
- [ ] bundle 仍携带各 kind 的可搜信息（接 task3）。

---

## 10. 决策记录（Owner 2026-06-08 已定）

- **A（实现细节，实现者自定）**：统一 `index()` 接口签名 + 投影 record_id 规则由实现时确定（确定性 + 幂等）。非 Owner 决策。
- **B（已定）**：索引侧 `content_text` **尽量全存**（user 输入 + agent final_output + 关键工具结果都加），**召回/使用侧再做选择裁剪**。
- **C（已定）**：
  - **job**：复用 JobModule **现有 after-hook**，在其中顺带 upsert 索引（只存 title+description+指针，幂等；实时状态走指针取，不进索引）。
  - **bus**：与 chat 同性质——**客观消息历史，append-only，一条消息一条索引，不管更新/去重**。
- **D（作废）**：原担心"当前对话线相关性不够、漏出 top-N"——已由 narrative selection（step 1）单独选中并注入当前线保障，无需被动注入额外兜底。
