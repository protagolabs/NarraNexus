---
code_file: backend/routes/agents/chat_history_timeline.py
last_verified: 2026-08-30
stub: false
---

# chat_history_timeline.py — event_log → timeline 的纯投影

## 为什么单独一个文件

从 [[chat_history]] 里搬出来的（2026-08-30）。那个路由文件 1141 行，远超
仓库约定的 800；而这一块**是纯函数、零 DB、零 request 上下文**，正是最容易
搬走的一块。不搬，下次再往那个文件加东西门槛只会更高——铁律 #8 说的
「烂成一堆」就是这么攒的。

搬出来的直接好处：`tests/backend/test_event_log_monologue_tier.py` 直接喂
step 列表断言分块与档位，**不必起数据库**。

## 两处判断（本文件真正的内容）

- **`is_monologue_step` 取「子集 == 并集」，不是 `bool(monologue)`。**
  混档步（`content` 是并集、`monologue` 只是子集、位置信息不存在）**现在只可能
  来自存量行**——[[_thinking_batcher]] 自 2026-08-30 起换档即 flush，新写入的
  帧天然 tier 纯净。相等判定保留，不是因为今天还会混，而是**万一哪天有路径把
  混档重新引进来，失败方向仍然是「漏提亮」而不是「把草稿纸提亮」**。
  同一条规则另有两份副本：前端 [[monologueTier]]（直播路径）与
  [[run_recorder]]`._extract_thinking_tier`（持久化路径），**三边必须一起改**
  （这份重复本身记在 followups 笔记第 2 条）。

- **换档强制 flush。** thinking 的合并（原本只为了不渲染 50 个小斜体块）
  在档位切换处断开：一个 timeline 条目只带一个 `monologue` 值，跨档合并会把
  两档文本标成一档。

## 上下游

- 唯一调用方：[[chat_history]] 的 `get_event_log_detail`
- 产出型别：`EventLogTimelineEntry`（[[api_schema]]），`monologue` 是
  `Optional[bool]` —— 与该 union 形状上其余「只对某一 type 有意义」的字段
  （`reply_via` / `tool_name` / `tool_output`）同一约定，tool_call 行不会平白
  序列化一个无意义的 `false`。
- 前端消费：[[segmentTurn]] 的 `timelineToEvents` 只透传，不重判。
