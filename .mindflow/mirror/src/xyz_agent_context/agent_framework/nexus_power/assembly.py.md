---
code_file: src/xyz_agent_context/agent_framework/nexus_power/assembly.py
last_verified: 2026-08-13
stub: false
---

## 2026-08-13 — expression_nudge 接线

LoopAssembly 新增 `expression_nudge: bool = False`；`run_turn_events` 把
`opts.expression_nudge` 接进 assembly——这是 TurnOptions→loop 的唯一接线点
（同 2026-08-07 确权链路教训：缺这一行前面三处都不生效）。机制见 loop.py.md 同日条目。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — `extra_readable_roots` 接到 ToolContext 上

`TurnOptions.extra_readable_roots` → `ToolContext`，供两个 confinement layer 消费
（见 [[policy.py]]）。**这里是整条确权放开链路的唯一接线点**：契约在
[[options.py]] / [[tooling.py]]，判定在 [[policy.py]]，而把值真正交到 ToolContext 手上的
是本文件。改动只有一行，但缺了它前面三处都不生效。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

PromptAssembler 的模式来源从硬编码 PromptMode.FULL 改为 PromptMode(opts.prompt_mode)——默认值不变，快速模式可按 turn 降面。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

ExpressionContract 前移到 expander 之前构建并挂 `add_expressive` 缝;
LoopAssembly.expression 直接用它(不再 frozenset 二次构造)。reminder 从装配时
算死的字符串变 `_tail()` 闭包:每步 `NexusPowerPrompts.reply_reminder(
expression.names())`——中途展开出的投递工具进 reminder,稳定前缀不动。
`PromptInputs.default_reply_tool` 取 `expression.names()` 首位(initial
expansions 之后取值,起跑展开授予的回复工具也算),装配时冻结。

# assembly — 唯一装配点:TurnRequest 进、类型化事件流出

LoopAssembly 是循环的全部依赖(硬组件无默认、策略缝带默认,R1:装配复杂度有意集中于此文件);run_turn_events 是框架顶层入口:装配→初始展开→跑循环。TurnRequest 整包可 JSON 序列化,这是 runner 跨进程传输的前提。测试用 dataclasses.replace 换件,永不 patch。坑:harness system 消息插在平台前导 system 段末尾(_insert_harness),不能追加在 user 之后;output_schema v1 显式 fail loud(schema 诚实)。
