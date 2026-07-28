---
code_file: src/xyz_agent_context/narrative/_narrative_impl/prompts.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — R4a：NARRATIVE_MAIN_PROMPT_TEMPLATE 拆出稳定版 + turn 版

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

新增 `NARRATIVE_STABLE_PROMPT_TEMPLATE`（= MAIN 模板逐字节减去
`- Updated At: {updated_at}` 与 `- Current Summary: {current_summary}` 两行）与
`NARRATIVE_TURN_PROMPT_TEMPLATE`（`## Current narrative state` 块，恰好承载这两
个每轮易变字段）。MAIN 模板**原样保留**——relocation 开关（见
[[context_runtime.py]]）关闭时走它，保证字节级回滚。**改共享文案必须同时改 MAIN
与 STABLE 两处**，等价性由 `tests/narrative/test_narrative_prompt_split.py` 锁定。

# prompts.py — narrative 子系统的全部 prompt 常量

## 为什么存在

narrative 子系统有多个 LLM 消费面：主 system prompt 的 Narrative 段
（PromptBuilder）、continuity 判定（ContinuityDetector）、检索仲裁
（NarrativeRetrieval 的 single/unified match）、元数据增量更新
（NarrativeUpdater）。所有 prompt 文本集中在这一个文件，逻辑代码零内联字符串，
prompt 审计只看这里。

## 上下游关系

**被谁用**：[[prompt_builder.py]]（type/actor 描述常量 + 三个 NARRATIVE_*_
PROMPT_TEMPLATE）；`continuity.py`（CONTINUITY_DETECTION_INSTRUCTIONS）；
`_retrieval_llm.py`（NARRATIVE_SINGLE_MATCH / UNIFIED_MATCH 两版）；
`updater.py`（NARRATIVE_UPDATE_INSTRUCTIONS）。

**依赖谁**：无。零 import，纯常量文件。

## 设计决策

- 8 个特殊 default Narrative 的边界规则写死在 continuity/unified-match prompt
  里——它们是平台级通用分类，不是场景逻辑（铁律 #4）。
- NARRATIVE_UPDATE_INSTRUCTIONS 强制 current_summary 为结构化 fact-sheet
  （bullet、8-12 条上限）而非散文——它每轮被 LLM 重生成，是 system prompt 增长
  的头号来源（见 [SYSPROMPT-BREAKDOWN] 的 nar_summary_chars）。
- 模板占位符与 Narrative 字段一一对应，注释块里逐个列出——加占位符必须同步
  prompt_builder 的 format kwargs，缺一个就 KeyError。

## Gotcha / 边界情况

- 文件里有**两处**关于 "Narrative main system prompt template" 的注释块头
  （历史遗留，第一处在 :40-52 只有注释无常量），真正的模板在文件尾部。
- MAIN/STABLE 模板以 `"\n## Narrative System"` 开头（前导换行），与
  context_runtime 的 `"\n\n".join` 组合产生刻意的段间距——改前导空白会静默改变
  system prompt 字节。
