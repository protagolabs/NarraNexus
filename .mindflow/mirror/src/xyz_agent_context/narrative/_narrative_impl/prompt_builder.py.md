---
code_file: src/xyz_agent_context/narrative/_narrative_impl/prompt_builder.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — R4a：narrative 模板拆分（稳定半 + turn 半）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`build_main_prompt` 新增 `include_volatile: bool = True`：True = 完整旧模板
（relocation 开关关时的字节等价路径）；False = 稳定版模板（id/type/created_at/
name/description/actors——CLI session 生命周期内恒定，narrative 切换 = 新
session），进 system prompt 可缓存前缀。新增 `build_turn_prompt(narrative)`：
渲染两个每轮易变字段（updated_at 每轮变、current_summary 每轮 LLM 重生成）为
`## Current narrative state` 块，进当前轮消息的 [Turn context]。actor 显示名
解析留在稳定半——display name 变更属合法打穿。拆分硬标准（稳定版 = 旧模板渲染
减去且仅减去两条易变行）由 `tests/narrative/test_narrative_prompt_split.py` 锁定;
改共享文案必须同步改 [[prompts.py]] 的两个模板。

## 2026-06-12 — actors rendered by HUMAN name (user_id is opaque in cloud mode)

`build_main_prompt` now resolves USER / PARTICIPANT actors to their human
display_name before rendering the actor list. Their `actor.id` is a `user_id`,
which in cloud mode is an opaque NetMind userSystemCode (32-hex) — showing it to
the LLM as a person is wrong. AGENT / SYSTEM actor ids are agent_id / system
keys and stay verbatim. Resolution goes through
[[user_repository.py]] `UserRepository.get_display_name(actor.id)` (the single
DRY id→name resolver), reached via `get_db_client()`. `get_display_name` falls
back to the id when there is no display_name / no such user, so nothing breaks
when an actor is unknown. Part of the Phase-1 user_name/user_id separation —
see [[basic_info_module.py]] for the canonical identity injection.

# prompt_builder.py — Narrative prompt assembly

## 为什么存在

`PromptBuilder` 把一个 `Narrative` 对象转换成给 Agent 推理用的结构化 system
prompt（main prompt）以及 summary prompt。它是 narrative 子系统对外暴露提示词
形态的唯一出口。

## 上下游关系

**依赖谁：** `..models`（Narrative / NarrativeType / NarrativeActorType）、
`.prompts`（各 type / actor 描述常量 + `NARRATIVE_MAIN_PROMPT_TEMPLATE`）、
以及 [[user_repository.py]]（actor 人名解析，运行时通过
`xyz_agent_context.utils.db.db_factory.get_db_client` 取 DB）。

**被谁用：** narrative 的 prompt 组装路径。`build_main_prompt` 是 async，因为
actor 人名解析需要查 DB。
