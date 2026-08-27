---
code_file: src/xyz_agent_context/narrative/_narrative_impl/merged_select.py
last_verified: 2026-08-27
stub: false
---

# merged_select.py — 合并路由路径的编排(从 service 层搬入 impl)

## 2026-08-27(round 6)— 观测面补齐 + 直调落点

best_score / scores 照两调用路径同样填充(minor 1:"下游无感"包括
开发者叙事面板);no_topic 落点直调 `landings.land_no_topic`
(minor 3,不再绕 service 委托);participant 渲染器不再二次截断
(minor 2:入口截断是唯一一刀,不变式从调用点约定升级为结构)。

## 2026-08-27(round 3)— landing 池与选票解耦;Landing 迁往 landings

**I2**:match 落地曾复用 menu_results 当尾随上下文池——MERGED_MENU_SIZE
是 env 旋钮,调成 1 会让 ChatModule 拿到的线从 3 条静默缩到 1 条,灰度期
的"回答质量下降"会被错归因给 router。现在落地池 = 全量排名剔锚点
(landing 深度只听 MAX_NARRATIVES_IN_CONTEXT),选票(渲染+契约)仍由
MERGED_MENU_SIZE 管。钉:test_the_menu_knob_does_not_shrink_the_match_landing。
`Landing` 与四个共享 executor 移居 [[landings]](M4:flag-off 路径不再
为一个 6 字段 dataclass import 合并模块的 helper-SDK 链)。

## 2026-08-27(round 2)— participant 入口截断 + prep 迁至 merged_prep

**I1**:契约按 `inp.participants` 校验索引,渲染只显示前 8 条——幻觉出的
`participant #11` 会落到从未上票的线且审计无从区分。现在入口先切前缀
(顺序=P0-4 优先级,只许前缀切片),**进 input 的 = prompt 显示的**;
截断标记由本文件补进 merged_truncated(渲染器不再见到被切的尾巴)。
钉子:`test_rule6_an_index_into_the_unrendered_participant_tail_is_refused`。
BM25 准备段改调 [[merged_prep]] 的模块函数(round 2 I6);LLM 计时打标改走
`agent_framework.llm.call_tagging.tag_last_llm_call`(I4,三处拷贝收敛)。

## 为什么存在

review 2026-08-27 Important 2:narrative_service.py 越过 800 行上限后又
并排放进第二个 255 行 decider,且 5 个 verdict 分支各自手写 6 个松散局部
变量,靠人眼保证赋齐。本文件是那次搬迁的落点:编排进 `_*_impl/`(本仓
分层约定),service 只留薄委托 `_select_merged`。

## 结构

- `Landing`(frozen dataclass,6 字段一次构造)——每个 verdict 分支返回
  一个完整 Landing,新增 result 字段漏赋会在构造点炸,不再静默取默认值。
  `_land_no_topic_turn`(仍在 service,select() 也用)同样返回它;select()
  只取四个决定字段,保住 flag-off 字节路径。
- `select_merged(service, ...)` 主编排:BM25 prepare → 快门或一次 LLM →
  `_land` 按 verdict 分发 → 会话锚点推进 → audit 落库。**决策语义与
  搬迁前逐字一致**(设计注释原样随迁)。
- `_land_failure`:RULE 6(失败不是判决)单独成函;participant 落地改走
  `NarrativeRetrieval.load_participant_landing`(review Minor 3,judge 与
  合并同一 executor)。

## 坑

- `is_reusable_anchor` / `minutes_since` 从 [[anchor_rules]] 导入——
  **必须保持唯一定义**(2026-08-21 review Important 3 的产物),别在这里
  长出第二份。
- 传入的是 service 实例(运行时协作),类型只在 TYPE_CHECKING 下引——
  impl 不得在 import 期上行依赖 service 模块。
