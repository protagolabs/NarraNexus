---
code_file: src/narranexus/platform/narrative/_narrative_impl/routing_blocks.py
last_verified: 2026-11-25
stub: false
---

# routing_blocks.py — 路由 prompt 的四个共享渲染块

## 2026-11-25 — 合并 LLM prompt 删除,只服务 continuity 与 judge

合并路径改用决策模型后不再渲染任何文本块。只为它存在的具名参数随之删除
(`render_anchor_context` 的 header / absent_note / summary_max_chars /
include_bucket_note,`render_previous_turn` 的两个 cap 与 absent_note),
`Rendered.truncated` 也删了——没有调用方再传 cap。continuity / judge 的渲染字节
不变(`test_routing_blocks.py` 的 golden 断言未改动且通过)。
`render_search_candidates` 的 header / empty_note / include_score 保留:judge 与
golden 测试显式传它们。`clamp_head` 保留,合并决策请求仍用它做读侧保头裁剪。


## 为什么存在

三个 tier 在向同一个模型描述同样的四件东西:**已锚定的那条线、上一轮、
BM25 菜单、participant 线**。历史上每一次"复制而不是共享",复制体都分叉了,
而且**只有一份被修**:

- judge 的 search 分支与 participant 分支曾是同一个决定的两份实现;
  2026-04-15 只有 search 分支迁到了活字段 `narrative_info`,另一份继续用
  创建时冻结的 `topic_hint` 标签(本地 dev 库 84% 为空),最长把一条 72 事件
  的线描述成三个月前的第一句话;
- 两份 judge prompt 就 no_topic 判据**分叉了三次**,最后一次是 PR #361
  review round 2 抓到的。

合并调用是第四个消费者 —— 正是这个模式会重演的时刻。所以块下沉到这里,
合并 prompt 曾是这些块的**组合**(2026-11-25 起合并路径改用决策模型,不再渲染文本)。

## 字节相同是硬契约

continuity 与 judge 必须渲染出**与本文件出现之前完全一样**的文本:它们的
prompt 被一堆实测数字钉着(M6 = 20.8%、P1 校准、description 退休干跑),
一个空格的变化就静默作废那些数字。曾经只为合并 LLM prompt 存在的具名参数已于
2026-11-25 删除(见上);剩下的 `render_search_candidates(header, empty_note,
include_score)` 由 judge 显式传值。

`test_routing_blocks.py` 里的 golden 断言(judge 菜单、judge 空菜单、continuity
锚点块、continuity 上一轮块)逐字节钉住老渲染。

## 读侧裁剪

`clamp_head` 只裁剪 prompt **看到**的内容,从不碰存储,而且永远保头:追问的
指代物("讲第一个" / "the first one")在 agent 上一轮回复的开头,裁尾会正好
丢掉上一轮存在的理由。被裁一定留标记 `…[truncated]` 并回报段落名 —— 隐形
裁剪会让模型把残句读成一句完整(而虚假)的陈述。

## 上下游关系

- **被谁用**: `continuity.py`(锚点块 + 上一轮块)、`_retrieval_llm.py`
  (菜单 + participant 区)、`merged_router.py`(只用 `clamp_head`)
- **依赖谁**: 只依赖 `models.Narrative` 的字段与 `loguru`。**没有** DB、
  没有 LLM、没有 config —— 预算常量由调用方传入,这样它是纯函数、可 golden 测

## 坑

菜单渲染里那条 `logger.warning`(有分数却没有证据)是从 `_retrieval_llm`
原样搬过来的报警,不是新加的。它对 `raw_score == 0` 的 participant 行**故意
不响** —— 那些行没走过 BM25,不欠证据;误报会让人把报警关掉,然后报警就永远
没了(事故教训 #3)。
