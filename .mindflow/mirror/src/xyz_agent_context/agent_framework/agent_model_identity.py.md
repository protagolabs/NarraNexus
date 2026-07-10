---
code_file: src/xyz_agent_context/agent_framework/agent_model_identity.py
last_verified: 2026-07-10
stub: false
---

# agent_model_identity.py — 解析 agent 真实的 (framework, model) 供 prompt 展示

## 为什么存在

系统 prompt 里那行 "Your LLM model: **X** (Y)" 曾经在 [[context_runtime.py]]
里被写死成 "Claude Agent SDK" / "sonnet-4"，于是**每个** agent 不管真实配置都
自称 Claude Sonnet-4，被问"你是什么模型"就照读（还违反铁律#9：身份绑死某框架）。
本模块把真身份从 runtime 实际派发用的 slot 行里解析出来，让 prompt 说真话
（如 codex_cli+gpt5 → "Codex CLI (gpt-5)"）。

放在 agent_framework 层而非 Module 里，是守铁律#9：[[basic_info_module.py]]
（一个 Module）只调 `resolve_agent_model_identity` 并渲染字符串，永远不认识
框架名。

## 设计决策

- **overlay 必须与 [[step_3_agent_loop.py]] 的 `_resolve_agent_framework_name`
  完全一致**：per-agent `agent_slots` 覆盖行只有在**带 `provider_id`**（真正
  rebind 了 slot）时才胜出，否则回退 owner（`agents.created_by`）的 `user_slots`。
  framework 和 model **都从同一个胜出的 slot 行读**（`agent_framework` + `model`
  两列，`schema_registry.py` 里 user_slots/agent_slots 都有），所以展示的身份
  和 driver 真正跑的一致。
  - 这里**故意复制**那份 overlay（~10 行）而不 import：`_resolve_agent_framework_name`
    在 agent_runtime 层，agent_framework 反向 import 它是层级倒挂。两处都很小、
    都有测试锁契约，改一处务必同步另一处。
- **绝不抛异常**：任何缺行/空列/DB 故障都降级到 `(claude_code, "")`，因为它喂的是
  system-prompt 构建路径，炸了会废掉整轮。降级值仍走同一 display 映射，宁可回退成
  一个"次真实"的默认，也不输出错误品牌。
- **未知 framework 名原样展示**（不塞进 `FRAMEWORK_DISPLAY_NAMES` 的名字直接回显），
  绝不替用户的私有框架名瞎编品牌。

## 上下游

- **被谁用**：[[basic_info_module.py]] `hook_data_gathering` 调它，填
  `ctx_data.agent_info_model_type`（framework 展示名）+ `ctx_data.model_name`
  （真实 model），再由 basic_info 的 [[prompts.py]] 模板 `{...}` 渲染进系统 prompt。
- **依赖谁**：只用 `db.get_one` 读 `agent_slots` / `agents` / `user_slots`。

## 契约测试

`tests/agent_framework/test_agent_model_identity.py`：覆盖胜出 / 无覆盖回退
user_slots / 缺 provider_id 不夺权 / 缺行→claude_code+空 model / DB 故障兜底 /
未知名原样。与 `test_resolve_agent_framework_per_agent.py` 用同一 `_FakeDB` 模式，
两者一起锁死 overlay 语义必须同步。
