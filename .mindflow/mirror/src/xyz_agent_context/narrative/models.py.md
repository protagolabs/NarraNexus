---
code_file: src/xyz_agent_context/narrative/models.py
last_verified: 2026-08-26
stub: false
---

# models.py — Narrative 模块所有数据模型的唯一来源

## 2026-08-25 — `pool_is_shadow`(切片 0 的分群标志)

`RoutingAudit` 加一列:这一行的池是**只记录、没参与决策**的(continuity 判 yes 的轮次)。

**为什么非要一个标志**:没有它,任何对 gate 列的聚合都会把两个人群混在一起 ——
"真决策"与"如果当时问了 gate 它会怎么说"。有了它,
`WHERE pool_is_shadow = 0` 是决策,`= 1` 是快门在续接轮上的可释放人群,
而后者正是合并设计拿不到的那个数(现在只能圈到 6%–39%,3 倍带宽全是重构松弛)。

**人群判别只认这一列**:`retrieve_ms` 现在在影子行也有值(仪器自己的耗时),
所以任何靠 `retrieve_ms IS NULL` 区分人群的历史查询都失效了 —— 判别统一收敛到
`pool_is_shadow`,这也正是它存在的理由。

**与 `gate_short_circuit` 的刻意不对称(铁律 #6)**:那一列的含义是
"gate 让这一轮跳过了 judge"。影子行里 gate 什么都没决定,所以它**保持 NULL**,
与今天逐字节一致 —— 不给既有读者改语义。假设性判决落在
`bypass_score_gate` / `bypass_reason`,那两列是本批次自己加的,没有历史读者。


## 2026-08-20 — description 是出生证,不是病历(墓碑字段修复)

新增 `Narrative.description_if_unsummarised()`,`searchable_text()` 只经它读
description。规则:**summary 是真病历时,出生证完全退出读取**(不是截断后继续读)。

**病**:description 创建时抄触发输入原文写一次、updater 永不重写,却在 BM25 索引里。
全表实测(1,381 条非 default):**291 条(21.1%)超 1500 字符,max 198,398**,
索引里 description 文本合计 2.5 MB。BM25 的 IDF/avgdl 在候选池自身上算,
一条 6KB 脏文档同时抬 avgdl(压死所有正常文档的长度归一化)并给自己灌进
大量可匹配 token —— 630 条真实判决离线重算:含化石的池免审率 **41.0%**
vs 不含 **14.5%**,3.7 倍,把它本该服务的臂级测量整个盖住。

**为什么是"退休"而不是"截断"**:截断后的化石仍是化石 —— 它仍在用现在时断言
一个可能几个月前就离开的话题。干跑对照(见
`data/replay_runs/2026-08-20/DESCRIPTION_RETIREMENT_DRYRUN.md`):
退休把 bloat 组压到 8.8% / max top1 152.6,截断 512 只到 15.3% / 252.1。

**条件为什么是"summary 非空"而不是"updater 跑过一次"**:updater 异步且会失败
(D-9 helper 哑火期出生的线永远拿不到 summary)。条件式规则自愈 ——
病历写出来→出生证退休;病历难产→出生证继续顶着,线不隐形。

### `PROVISIONAL_SUMMARY_PREFIXES` 是这条规则的地雷区

`NarrativeCRUD.create` **不留空** `current_summary`,它写
`"Newly created Narrative: {title}"`;default 桶写 `"This is a default …"`。
所以"summary 非空"**不等于**"这条线有病历"。按字面实现的话,出生证会
**在出生瞬间退休**,而自愈分支一次都不会触发 —— 恰好在它被设计来保护的
那个场景上失效。这个坑是读代码发现的,干跑测不到(干跑用的是跑批结束时的
字段状态,全是真 summary)。

前缀只有一处定义,写方(crud)与读方(本文件)同一份;两份字面量会静默漂开,
而唯一症状是**新线悄悄变得找不到**。


## 2026-08-20 — `RoutingAudit` 多两列,而且它们不是重复列

`bypass_score_gate` + `bypass_reason`。

**为什么不复用 `gate_short_circuit` 一列了事**:那一列的语义从第一天就是
"这一轮跳过了 judge",Q 上线后它仍然如实表达这件事(所以**没有**发生
铁律 #6 禁止的"静默改变既有列语义")。但 floor+margin **单独**的判定
从此不再等于它 —— 而那正是层 2 要标定的序列。
少了 `bypass_score_gate`,Q 上线那天就是分数门分布停止积累的那天,
下一个决策会没有数据。两列内容不同,不是冗余。

`bypass_reason` 是**稳定机器码**(七个值,见 routing_gate 的 mirror),
拿它 join `judge_category` 就能回答"被锚点规则拒掉的那些轮,judge 最后判了什么"
—— 即这条规则值不值。所以它必须是枚举式短码,不是自由文本;
自由文本在 `gate_reason` 里(那一列现在存 `BypassDecision.detail`)。

## 2026-08-16 — NarrativeSelectionResult.no_durable_topic（C-1）

judge 的"这一轮没有可沉淀话题"是一个**关于轮次的标签**，不是目的地。它带着空的
narrative 列表从 retrieval 返回，由 `NarrativeService.select` 决定落点
（anchor-first）；随后一路传到 `step_4`，在那里的含义是**把 event 记上，但不许这
一轮改写这条线的检索面**——一句寒暄不能给它打断的工作改名。
## 2026-08-14 — `RoutingAudit` 新增四个耗时字段

`continuity_ms` / `retrieve_ms` / `keyword_ms` / `judge_ms`，默认 `None`（= 这一层
没跑），永远不用 0。理由见字段旁注释与 `narrative_routing_audit_repository`。

## 2026-08-12 — `NarrativeSearchResult` 带上匹配证据；`topic_hint` 定性为墓碑

`matched_terms` / `matched_snippet` 两个新字段：BM25 命中的词（按贡献降序）和它们在
被打分文本里的上下文。填充点是 [[retrieval.py|_narrative_impl/rank_pool]]，消费点是
LLM 判官。放在这个模型上而不是另造一个载体，是因为它和 `raw_score` 是**同一次
BM25 计算的三个输出** —— 分数、分数的来源、来源的上下文，分开传就会分开腐烂。
participant narrative 从不经过 BM25（合成中性分入池），两个字段留空。

`topic_hint` 的注释从"Topic hint/summary"改成它的**真实语义**：创建时由
`_create_narrative` 写入的被截断的第一句 query，2026-06-09 之后永不更新。同时
类 docstring 里的 "Routing Index: topic_hint, topic_keywords (BM25)" 是**错的** ——
BM25 打的是 `searchable_text()`，而它不含 topic_hint，已改。写下这条是因为字段名
和旧注释合起来会让人以为它是"当前话题摘要"，而 B2 就是这么发生的：判官被喂了三个
月前的第一句话（见 [[retrieval.py]] 2026-08-12）。它作为**创建时来源**展示是诚实的
（`backend/routes/me.py` 的 timeline 卡片），作为**当前状态**参与任何决策都不是。

## 2026-08-07 — `Narrative.searchable_text()`：BM25 文本面的唯一定义

原来有两份拷贝：`retrieval.load_pool`（`" ".join`）和 `crud._index_narrative`
（`"\n".join`）。当前分词器按空白切，所以两者等价——**只差一次分词器改动就是真
bug**，而路由和 `remember` 会就「这条 narrative 讲什么」给出不同答案，且所有测试
照常绿。

放在模型上而不是抽成 `_narrative_impl` 里的公共函数，是因为 `retrieval` 已经
import `crud`，反向 import 会成环。

（原本 `retrieval._searchable_text` 的 docstring 写着「the one definition」，但那
一刻第二份拷贝还活着——手册「Claims in docs are code too」讲的就是这种：写下
only / always / never 之前先 grep 反例。）

## 2026-08-07 — RoutingAudit / RoutingCandidate（E1）

路由决策的载体。`RoutingCandidate.text_hash` 指向 narrative **被打分那一刻**的文本，
不是它现在的文本——`name / current_summary / topic_keywords` 被 [[updater.py]] 几乎
每轮全量重写且不留历史，事后回读 `narratives` 表重建的池子是一个从未存在过的池子。

`RoutingAudit.candidates` 刻意装**整个池子**而不是 top-K：`bm25_rank` 的 IDF 和
avgdl 在候选集自身上算，裁过的池子重放出来是另一组数。详见
[[narrative_routing_audit_repository.py]]。

`NarrativeSelectionResult` 上新增的 `audit` / `audit_snapshots` 是**临时字段**，只在
retrieval tier 和 `NarrativeService.select` 之间传递，不随任何东西落库。挂在结果对象上
而不是另开返回值，是为了让调用点没法忘记它。


## 2026-07-31 — TriggerType 与 WorkingSource 1:1 对齐

新增 JOB/A2A/CALLBACK/SKILL_STUDY/LARK/SLACK/TELEGRAM/WECHAT/
NARRAMESSENGER/DISCORD/MANYFOLD 成员：step 0 现在把 working_source
直接映射进 `events.trigger`（原来除 message_bus 外一律记 chat，lark/job
run 全被标成"聊天"）。读侧依赖这个诚实标签：侧栏预览滤 MESSAGE_BUS
（不变）、**聊天页 active_run 自动接管只认 chat/manyfold**（否则 trigger
run 变 running 后会被单聊页面劫持，见 [[auth]]）、dashboard 按来源分组。
repository/crud 的 `TriggerType(row["trigger"])` 回读要求新值必须是合法
成员 —— 这是扩枚举而非直接存字符串的原因。

## 2026-07-29 — `NarrativeSearchResult.raw_score`

新增字段，承载未 squash 的 BM25 原始分。`similarity_score` 保留给展示和 LLM
prompt，但**判据不能用它**：`s/(s+1)` 压缩了候选之间的间距，而间距是这里唯一
可比的信号（IDF 按候选集现算，绝对值无跨 agent 意义）。见 [[routing_gate.py]]。
参与者 narrative 走合成中性分、无 BM25 分，`raw_score` 保持 0.0。

> 2026-06-23：`TriggerType` 新增 `MESSAGE_BUS = "message_bus"`，用于把团队群聊
> (message bus) 的 Event 与 1:1 聊天区分开（侧栏预览据此过滤；见 [[event_service]]
> / [[step_0_initialize]] / [[auth]]）。这是 `Event.trigger` 用的枚举（CHAT/TASK/
> API/TOOL/MESSAGE_BUS/OTHER）——注意另有一个同名 `WorkingSource`/`module_schema`
> 的 `TriggerType`，不是这个。

> 2026-05-29：删除 `EpisodeResult`，并从 `NarrativeSearchResult` 去掉
> `episode_summaries` / `episode_contents` 字段（EverMemOS 整体移除）。


## 为什么存在

Narrative、Event、ConversationSession 三个核心数据结构原本分散在多个文件里，导致跨文件循环引用频繁发生。合并到 `models.py` 这一个文件后，任何需要这些类型的地方都只需要 `from .models import ...`，消除了模块内循环导入。

同时，这个文件也是理解整个记忆系统的最佳起点——读完这里的类定义，就能理解系统是如何组织记忆的。

## 上下游关系

**被谁用**：`narrative/` 包内所有文件都从这里导入类型；`agent_runtime/` 的 step 文件通过 `NarrativeService` 间接使用；`repository/narrative_repository.py` 和 `repository/event_repository.py` 用于数据库序列化/反序列化；`services/instance_sync_service.py` 用 `NarrativeActor` 和 `NarrativeActorType`；schema 层的 `ModuleInstance` 被 `Event.module_instances` 引用。

**依赖谁**：只依赖 Python 标准库和 `xyz_agent_context.schema.module_schema.ModuleInstance`。模型层自身是"纯数据"，不引用任何实现逻辑。

## 设计决策

**Narrative 是路由索引，不是内容容器。** `Narrative.routing_embedding` 是用来"找到这条线"的，`event_ids` 是指向事件列表的引用而非事件内容本身。实际的对话内容存在 Event 里，Narrative 只存摘要（`topic_hint`、`dynamic_summary`）。这个设计让 Narrative 对象保持轻量，可以整体加载进内存；Event 按需批量加载。

`NarrativeActorType.PARTICIPANT` 是 2026-01-21 新增的类型，专门支持"目标客户"场景——Job 的目标用户会以 PARTICIPANT 身份加入 Narrative 的 actors，让该用户发消息时也能匹配到这条 Narrative。这条逻辑在 `services/instance_sync_service.py` 的 `_add_participant_to_narrative()` 里实现。

`Narrative.main_chat_instance_id` 字段标注为 Deprecated（2026-01-21），保留仅为数据库兼容性，不要在新代码里读写它。

`NarrativeSelectionResult.evermemos_memories` 是 Phase 2 引入的 EverMemOS 缓存透传字段，格式自由度高（`Dict[str, Any]`）。如果 EverMemOS 未启用，这个字段是空 dict，不影响正常流程。

## Gotcha / 边界情况

`Narrative.is_special` 字段默认是 `"other"`，只有系统预置的 8 个默认 Narrative 会被设为 `"default"`。`ContinuityDetector` 对 default Narrative 有更严格的判断逻辑（一旦用户提到具体话题就切换 Narrative）。如果通过 API 手动创建 Narrative 并设置 `is_special="default"`，会导致这条 Narrative 被连续性检测器异常对待。

`Event.env_context` 是自由 dict，里面存了模型名、执行参数等信息。`EmbeddingMigrationService` 在重建 Event embedding 时会从 `env_context.input` 字段读取输入内容，字段名必须匹配——如果某个触发路径没有在 `env_context` 里写入 `input` key，该 Event 的 embedding 重建会退化到用 final_output 估算。

## 新人易踩的坑

`ConversationSession` 和 `Narrative` 的关联是单向的：Session 持有 `current_narrative_id`，但 Narrative 里没有"谁的 session"字段。查"某用户的当前 Narrative"要通过 SessionService，不要去查 Narrative 表。

`NarrativeSearchResult` 的 `episode_summaries` 和 `episode_contents` 是 EverMemOS 的专有字段，在纯向量检索路径下始终为空列表，不代表 Narrative 没有事件。
