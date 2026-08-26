---
code_file: src/xyz_agent_context/narrative/_narrative_impl/retrieval.py
last_verified: 2026-08-25
stub: false
---

## 2026-08-20 — 免审改由锚点决定(Q 层)

`_retrieve_top_k` 的 `if gate.short_circuit and not has_participant_narratives`
换成 `evaluate_bypass(...)` 的一个决定。两个实现细节值得记:

1. **`top1_narrative_id` 不是 `search_results[0]`。** participant 合并会在排序后
   追加合成 0.5 分的条目并按 similarity 重排,所以 0 号位可能是一条**从未过
   BM25**的线。锚点比对必须拿**关键词赢家**(`max(..., key=raw_score)`,
   且 `raw_score > 0`),否则比的是噪声。
2. **`gate_reason` 现在存 `BypassDecision.detail`**,分数门自己的句子被包在
   里面(`score_gate` 分支直接透传 `gate.reason`)。要机器可分组的口径请用
   新的 `bypass_reason` 列。

范围与代价见 routing_gate 的 mirror。

## 2026-08-20 — BM25 的查询面清理(K 层)

`rank_pool` 在喂给 `bm25_explain` 之前先 `strip_routing_prefix(query)`。

**为什么剥在 `rank_pool` 里、不在调用点**:这里是 narrative 路由**唯一**的 BM25
入口,`retrieve_top_k` / `keyword_search` / `select_fast` 全部经过它,一处生效
三处受益;而且这个方法的既有契约是"重放与实时判决跑逐位相同的代码",把剥离放在
函数内部,重放依然逐位可复现。**审计行仍存原始 `query_text`** —— 用户说了什么是
记录,BM25 打的是什么由同一个函数从记录推导得出。

`create_from_query` 的 name / description 也改用剥离后的文本(见 updater 的
mirror:磁铁线的另一半来自建线命名)。`topic_keywords` 那一行**没动**,A-kw 未定。

实测依据:`specs/2026-08-20-bm25-gate-redesign-research.md` §2.8 / §R2.1。

## 2026-08-16 — default 桶退出路由（C-1 方案 ④）

三处过滤，全部挂 `config.NARRATIVE_DEFAULT_BUCKETS_ENABLED`：

- `load_pool`：桶不再进 BM25 池。它的 `searchable_text()` 是冻结模板、**永远不可能
  正当地赢下一个查询**，但它照样通过 IDF/avgdl 影响别人的分数，还能自己短路 gate
  （重演里实测 2 轮）。把它请出池子，后面几件事才谈得上诚实。
- `_ensure_default_narratives`：不再为新 (agent,user) 播种。存量行不动（铁律 #6）。
- `_llm_unified_match`：不再向 judge 传 `default_candidates`。八条固定项**还带
  Examples**、对最多三条动态项——这是一份会自己回答自己的菜单（实测 judge 60% 的
  裁决选了桶，其中 63/93 次池里明明有真实候选）。

新增第 4.5 段返回分支：judge 判 `no_topic` 时**带空列表返回**，不在这里创建。
落点取决于 session 锚点与该 surface 是否持久化历史，这两件事在 retrieval 内部不可
知——决定权在 `NarrativeService.select`（见其 mirror 的 anchor-first 条目）。在这
里创建，正是本批次要消灭的碎片化。
## 2026-08-14 — `_create_narrative` 公开更名 `create_from_query`

行为零变化，纯改名+两处内部调用同步。动机：NarrativeService.create_fast
（chat fast mode durable miss 路径）需要同一套查询式创建（BM25 路由面
一致），跨 facade 调私有方法不如给它正名。


## 2026-08-14 — 两个独立读并发 + `keyword_ms` / `judge_ms` 落审计

**先说结论，免得误导后来人**：这个并行**不是**叙事选择的延迟修复。实测（本地真机
`[TIMED]`，改造前的 span 名）：`ensure_defaults` 4.3ms、participant 查询 3.1ms、
`keyword_search` 4.5ms——三者合计 12ms，而 setup 段 p50 是 **8.5 秒**。

**span 名已变**：两个独立读并发之后，原 `narrative.retrieve.participant_query` 改名
`narrative.retrieve.independent_reads`，量的是 max(participant, pool)。拿旧名去 grep
历史日志做前后对照会得零命中——而"扒日志做前后对照"正是本项目自己示范并写在下面的
方法。

真正的成本是两侧的 helper LLM：continuity ~3.9s、unified judge ~4.7s。谁来这里想让
选择变快，应该去看那两个。

**`ensure_defaults` 不能进 gather**，这是**正确性**约束不是偏好：它在默认 narrative
缺失时会**创建**它们，pool 读若与创建竞争，BM25 候选集会静默漏掉它们——那是错答案不是
慢答案。`tests/narrative/test_retrieval_concurrency.py` 第一条就锁这个顺序。

participant 查询（`_get_participant_narratives`）与 `load_pool` 互不喂给对方，所以并发。**裸协程直接进 gather，不包
`create_task`**：gather 本身就并发调度，自己持 Task 句柄只会白多两个对象。变异检验证实：
包不包 create_task 行为一致，退回**纯串行**才会让「重叠」那条测试挂。

（更正一处早先写反的说法：`gather(return_exceptions=False)` **不会**取消未完成的兄弟协程
——官方文档明示。所以"少一条『一个失败后另一个还活着』的路径"是错的；实际代价只是一次
已无人接收的 DB 读继续跑完，不会产生教训 #2 那种 GC warning。）

`return_exceptions` 保持默认 False：任一读失败意味着候选集**不完整**，拿剩下的自信地路由
是"把错答案打扮成对答案"。

`keyword_ms` 严格等于 **pool 读 + rank**，不含它现在并排跑的 participant 查询：
`load_pool` 在 gather 内用 `_load_pool_timed` 单独计时，与 `_rank_ms` 相加。第一版把
时钟起点放在 gather 之前，于是这一列把 participant 的耗时记到了 BM25 头上——而它要回答
的问题恰恰是「BM25 会不会成为瓶颈」。`tests/narrative/test_retrieval_concurrency.py::
test_keyword_ms_excludes_the_participant_read` 用一个慢 250ms 的 participant 查询锁住这
条。`judge_ms` 在被判决路径的唯一出口处设置，同文件有测试断言判决路径非 NULL。

## 2026-08-12 — judge 拿到 BM25 证据（B1）+ 候选标签只有一份（B2）

**B1 —— 判官以前只看到一个数字，而那个数字会骗人。** 候选喂给
[[_retrieval_llm.py]] 的信息是 `Similarity score: 0.91`。这个数是
`s/(s+1)` 压出来的，压之前的 IDF 在候选集自身上算，**绝对值没有跨 agent
意义**；更要命的是中文按字切 unigram，请求框架字（帮/查/一/下/天）会实打实
攒出分数。实测本地一条 query「帮我查一下明天上海的天气怎么样」对一条会议纪要
narrative 打出 raw 10.67，逐词分解 **100% 来自框架字**，承载话题的
明/上/海/气/样 贡献恰好 0 —— 压缩后显示 0.914。而闸门把这一轮交给判官的**原因
恰恰是候选拥挤**（top1/top2 = 1.08 < 2.0）：系统在最需要精细判别的时刻，交出去
的是最粗的信息。

修法是把已经算出来的东西传下去，不是新增计算：`rank_pool` 改调
[[retrieval.py|memory 的 bm25_explain]]（同一套算术，分数到最后一位都相同，
额外拿到每个词的贡献），填进 `NarrativeSearchResult.matched_terms` /
`matched_snippet`，`_llm_unified_match` 组候选时带上 `matched_content`。
**零新增 IO、零新增 DB 查询** —— 被打分的文本此刻正在手上，而事后重建是不可能
的（[[updater.py]] 每轮全量重写且不留历史）。成本是判官那 45% 轮次里每候选多
约 200 字 prompt。

读取侧的代码从 2026-03-06 就写好了，写入侧 2026-04-15 被删（数据源换成
episode_summaries），两侧在不同文件里，于是 `if candidate.get('matched_content')`
**忠实地走了将近 4 个月的 else 分支**，`logger.debug("has no matched_content")`
每轮都在打 —— 警报一直响，没人听。现在 else 分支改成 `logger.warning`：search
候选必然来自 BM25，snippet 不可能合法为空，它再响就是接线又断了。

**B2 —— 同一件事两份实现，只改了一份。** participant 分支读
`narrative.topic_hint`，而 50 行以上的 search 分支 2026-04-15 就改读
`narrative_info` 了。`topic_hint` 在 2026-06-09 unified-memory 重构后是**创建时
写一次的墓碑字段**（本地库 84% 为空）。于是判官看到的是
`[Participant-0] Untitled / Description:`（空的那 84%），或者 72 个 event 的活跃
线索被它三个月前第一句话描述，或者 `[:50]` 正好切在 open_id 中间。**而这条通道
是强制走判官的**（别人邀请你参与的任务不该输给你自己 narrative 的一个高 BM25
分），标签盲了等于这一轮的判断盲了。

修法不是「把 participant 分支改成和 search 一样」—— 那样下次还会漂。新增模块级
`_candidate_labels(narrative) -> (name, description)`，**两条分支物理共用一个
函数**；`test_participant_and_search_branches_share_one_labeller` 钉的是两条分支
**输出相等**，所以将来任何单边修改都会红。同时删掉 `_prepare_candidates`（第三份
拷贝，也在读 topic_hint）及其整个死代码簇。

`topic_hint` 至此在路由层与 narrative prompt 层零读取，只剩
`_create_narrative` 的那次写入 + `backend/routes/me.py` 的前端展示 —— 后者是
诚实的（它展示的正是"这条线索是从哪句话开始的"）。测试
`test_no_narrative_labelling_path_reads_the_frozen_topic_hint` 用 AST 检查
Load 上下文的属性读，写入不算。

## 2026-08-07 — 文本面上移到 `Narrative.searchable_text()`

`_searchable_text` 静态方法删除，`load_pool` 和 `_record_pool` 都改调模型方法。
详见 [[models.py]]：这个定义必须和 `crud._index_narrative` 严格同一份，否则路由
和 `remember` 会不一致。

## 2026-08-07 — 池子拆出 `load_pool` / `rank_pool`，为的是审计能精确重放

`keyword_search` 内部原来一口气做完「读 narrative → 拼文本 → BM25」。现在拆成
`load_pool`（读 + 拼，返回 `(id, text, is_default)`）和 `rank_pool`（纯函数打分），
`keyword_search` 保持签名不变——它是 `select_fast` 依赖的公开接缝。

拆的原因不是整洁，是**审计必须存下被打分的那份文本和完整池子**：`bm25_rank` 的
IDF 和 avgdl 都在候选集自身上算，存 top-K 重放出来是另一组数。2026-08-07 实测 452
条真实本地 query，仅仅移除 8 条 default narrative 就让 top-1 翻转 9.7%、闸门决策
翻转 5.8%——那 8 条语义上毫不相关，却真实地改变了排序。

`retrieve_top_k` 变成薄包装，内层是 `_retrieve_top_k`。这么做是因为决策出口有 7 个
（本方法 + `_llm_unified_match`），在每个出口分别拼装审计是必然会腐烂的记账——将来
加一个分支就静默失去可观测性，而这正是这张表要终结的失败模式。外层只在**一个地方**
盖上结局章。

判官的 `reason` 原文也存下来了：它是流水线里唯一的语义检查，而它的推理过程以前只
活在一个进 loguru 的 f-string 里。


## 2026-08-06 — `_keyword_search` 转正为公开 `keyword_search`

F28 快速模式的 `NarrativeService.select_fast` 需要「BM25 top-1、零 LLM、零新建」的最小召回，直接依赖这个方法——service 层不允许下探 impl 私有名（review #6），故私有转公开。按铁律 #2 不留 `_keyword_search` 兼容别名。**它现在是被 service 依赖的公开接缝**：改签名/语义前先看 `narrative_service.select_fast`（含 NARRATIVE_MATCH_RAW_FLOOR 门槛逻辑）。
# _narrative_impl/retrieval.py — 把一句用户输入路由到某条会话线

## 2026-08-25 — `record_pool_only`:建池、打分、然后交给没有人(切片 0)

新增一个**零决策**方法。它做的事与真实路径逐字相同(participant 查询 + `load_pool`
并发、`rank_pool`、`_record_pool`、`evaluate_gate`、`evaluate_bypass`),
差别只有一个:**什么都不返回**,结果只落进 audit 行。

**为什么需要它**:`NarrativeService.select` 在 continuity 判 yes 时提前返回,
于是这些轮次从来没有池。这是零 LLM 快门的可释放人群只能圈到
**6%(下界)–39%(上界)** 的唯一原因 —— 3 倍带宽是重构松弛,不是信号。
见 `specs/2026-08-25-merged-routing-design.md` §2.2。

**刻意不调 `_ensure_default_narratives`**:那个函数会**创建**行,而仪器不该为了观测
去写业务数据。C-1 治理下桶本来就不进池,所以记下来的池就是真实路径会打分的那个池。

**同步 await,不是 fire-and-forget**:两次 DB 读、实测 ~13.5ms/轮。
`create_task` 在这里会把异常吞成一条 GC 警告,还会与下面那次 audit 写入抢跑
(事故教训 #2)。


## 为什么存在

`narrative_service.select()` 只在**连续性检测判定不连续**时才走到这里。本文件
负责回答"这句话属于哪条已有会话线，还是该新建一条"。无向量——embedding 早已
退役，召回是 BM25 + participant 查询 + LLM 仲裁。

## 三层结构

1. **候选召回**：`keyword_search` 用 [[retrieval.py|memory 的 bm25_rank]] 对
   每条 narrative 的 `name + current_summary + description + topic_keywords`
   打分；`_get_participant_narratives` 另外捞出"用户是参与者"的会话线（关键词
   搜不到它们），以合成中性分 0.5 入池。
2. **判据**：[[routing_gate.py]] 决定 BM25 够不够格自己拍板。够 → 直接返回
   Top-K；不够 → 落到第三层。
3. **LLM 仲裁** `_llm_unified_match`：看候选 + default narrative + participant，
   判"匹配哪条 / 匹配 default / 都不匹配就新建"。**这是唯一的语义检查。**

## 2026-07-29 — 判据从绝对值改成 floor + margin

原来是 `best_score >= 0.70`，比的是 `_keyword_search` squash 后的
`s/(s+1)`——代数上等价于原始分 2.33。中文单字 unigram 下几乎恒真：273 条真实
prod 轮次实测**短路 87.5%**，第三层等于死代码，误路由直接落地并被
`narrative_service` 的 `continuous` 路径锁定多轮。

现在读 `NarrativeSearchResult.raw_score`（新增字段）交给 `evaluate_gate`。
同一批数据短路率 48.0%。定值依据见 [[routing_gate.py]]。

## 坑

- **participant 分支必须绕过短路**（`not has_participant_narratives`）：那些
  narrative 带的是合成中性分，且业务上"别人邀请你进来的任务"应当压过"你自己
  narrative 的高 BM25 分"（P0-4）。改判据时别把这个条件顺手删了。
- 高置信短路返回的是 **Top-K 而不是 Top-1**——第 2、3 条会进
  [[step_4_persist_results.py]] 的辅助 narrative 分支，**每轮复制一份 event
  行**（近 7 天全平台 33.9% 的 event 行是复制品）。这是已知问题，未在本次修复
  范围内。
- `dynamic_summary` 无上限累积，narrative 越大可检索文本越长、越容易赢下任意
  查询——正反馈。同样是已知问题，见诊断报告（本次未修）。
- **participant narrative 会在判官 prompt 里出现两次**（2026-08-12 发现，未修）。
  Step 1.5 把它们并进 `search_results`（合成相似度 0.5）再整体重排，于是
  `search_results[:3]` 可能含 participant 行 —— 它同时出现在 `## Participant-
  Associated Topics` 和 `## Existing Topics` 两个块里。BM25 分弱的轮次（正是走判官
  的轮次）0.5 很容易挤进 top-3，所以这不是罕见路径。改前两处标签不同
  （topic_hint vs narrative_info），改后**完全相同**，重复因此更显眼。没在本次修，
  因为动候选构成会改判官的 index 映射与闸门输入，属第二波（C1）的风险面。
  直接后果之一：这类行 `raw_score == 0.0`，所以"缺证据"告警必须按 `raw_score > 0`
  过滤，否则每个 participant 轮次都误报（见 [[_retrieval_llm.py]]）。
