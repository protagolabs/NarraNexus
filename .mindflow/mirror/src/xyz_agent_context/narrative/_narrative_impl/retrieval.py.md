---
code_file: src/xyz_agent_context/narrative/_narrative_impl/retrieval.py
last_verified: 2026-08-14
stub: false
---

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
