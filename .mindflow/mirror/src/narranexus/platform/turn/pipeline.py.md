---
code_file: src/narranexus/platform/turn/pipeline.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3b）— `resolve_profile(explicit=)`

回合显式点名的 profile（TURN 绑定层）优先级最高，必须在 `turn.profiles` 注册表或内置表里，否则 `UnknownEntry`。

## 2026-09-04（批 3a）— `TurnPipeline`：七阶段唯一的编排器

对每个阶段：`onWill<Stage>` 钩子（收冻结输入视图；返回值改写留给后续批）→ profile 指定的策略
（`turn.pipeline.<stage>` 注册表按名取，没人提供即 `UnknownEntry` 炸响）→ `onDid<Stage>`（冻结输出视图）。
阶段消息原样流给调用方；钩子受 profile 同步预算限制且永不让回合失败；Ingress 置 `aborted` 则停。
`resolve_profile` 把遗留旗标映射到内置 profile：silent > 显式 TurnProfile 名（含 voice→voice，bm25→fast）> fast_mode
> 来源 job > default，先查 `turn.profiles` 注册表再回落内置表。`PIPELINE_CONTRIBUTION` 填 `turn.pipeline` 位。
