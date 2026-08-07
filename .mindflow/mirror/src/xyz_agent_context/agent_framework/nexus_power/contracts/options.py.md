---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/options.py
last_verified: 2026-08-07
stub: false
---

## 2026-08-07 — TurnOptions.extra_readable_roots

框架外部调用面新增：本回合额外可读的绝对根，由调用方（知道 user/team 的那一层）决定。
保持框架通用性（铁律 #9）——这里不出现 team/user 概念，只有「这些根也允许」。
缺省空 = 纯 workspace 收敛。


## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

新增 `prompt_mode: Literal["full","minimal","none"]="full"`（字符串字面量，wire 层禁 import PromptMode 枚举；assembler 侧转换）。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`ExpandableSpec` 新增 `expressive_tools`(wire 形状,随包声明投递工具);
assembly 翻译进 `Expandable`。`TurnOptions.expressive_tools` 语义强化:
**首位=默认回复工具**(constitution 例子),平台按优先级序传入。

# contracts/options — TurnOptions 对外参数面(pydantic)

深度对齐 claude-agent-sdk / codex exec(cwd/output_mode/output_schema/permission_mode/subagents/expandables;映射表在 docstring)。故意没有 max_turns(铁律 #14,永不提供)。重大坑:pydantic v2 的 model_* 是保留命名空间——曾用 model_extra 撞上 BaseModel 内建属性(恒 None),已改名 llm_extra;今后新增字段禁用 model_ 前缀。
