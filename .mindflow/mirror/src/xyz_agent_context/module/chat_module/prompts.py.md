---
code_file: src/xyz_agent_context/module/chat_module/prompts.py
last_verified: 2026-04-22
---

# prompts.py — ChatModule 指令定义

## 为什么存在

`CHAT_MODULE_INSTRUCTIONS` 的核心使命是建立 Agent 的"思考 vs 说话"认知模型，
明确**"说话"唯一通道是 MCP 工具 `mcp__chat_module__send_message_to_user_directly`**，
并按 `working_source` 分层规定"必须说话"和"酌情说话"。这是整个系统里最关键的
prompt 约束之一——如果 Agent 没有正确理解"不调用这个 MCP 工具就等于没说话"，
用户将永远看不到 Agent 的回复。

## 上下游关系

- **被谁用**：`ChatModule.__init__` 把它赋给 `self.instructions`；每轮对话时注入系统提示
- **依赖谁**：无外部依赖，纯文本常量
- **对齐对象**：`xyz_agent_context/schema/hook_schema.py::WorkingSource` 枚举
  （prompt 里的 trigger 分层表格必须与枚举值一致）

## 设计决策

**显式写出 MCP tool 全名**：2026-04-22 之前 prompt 只用裸名 `send_message_to_user_directly`，
但 Claude Agent SDK 对 MCP 工具的注册名是 `mcp__<server>__<tool>` 格式，
真实名字是 `mcp__chat_module__send_message_to_user_directly`。裸名在 ToolSearch /
deferred tool loading 机制下会命中不到，LLM 会在 thinking 里抱怨 "tool not
available" 然后放弃说话。现在 prompt 开头明确给全名，并在 anti-patterns 段
专门反模式化 "搜裸名得到空结果就放弃"。

**按 `working_source` 分层定义说话纪律**：旧版 prompt 把 "用户直接对话 / background
job / IM 频道" 混在一个 Scenarios 表格里且用的是口语化分类，LLM 很难精确映射到
实际的 `working_source` 值。新版把 7 个枚举值（`chat` / `job` / `lark` /
`message_bus` / `a2a` / `callback` / `skill_study`）——每一个都列出来：只有
`chat` 是 ✅ 必须调用，其余都是 ⚖️ Agent 自行判断。这是"区别对待用户主动会话
vs 后台自动触发"的核心。

**"chat turn 必发"是最强硬约束**：为了修 Bug T1 (silent turn) 的模式 A/B，
在 trigger 表格 + Anti-Patterns + Guidelines 三处重复强调 `working_source=chat`
必须以一次 `mcp__chat_module__send_message_to_user_directly` 调用结束。重复是
有意的——LLM 在 long-tool-chain 场景下容易遗忘，冗余强调能显著降低遗忘率。

**用表格和类比强化核心概念**："你的文本输出用户看不到"是反直觉的（大多数 LLM
训练数据里 final_output 就是用户可见回复）。Prompt 用了声音隔离室类比和明确的
对比表格来强化这个概念。这些重复强调是必要的，不是冗余。

**Anti-Patterns 列表**：明确列出错误做法（转发每条 IM 消息、发送进度更新、
重复确认收到任务、把 `working_source=chat` 静默结束、搜裸名放弃）比只说
"什么时候发"更有效。负面示例对 LLM 行为约束效果更强。

## Gotcha / 边界情况

- **`working_source=chat` 的 final-answer 约束**是为了防止 Agent 做了大量工具
  调用和研究，但最后忘记调用 MCP 工具总结结果。这个约束和"不要发没必要的消息"
  之间存在张力，Agent 需要判断平衡点——但只对 `chat` 必发，其它 source 默认静默。
- **MCP 工具名前缀的可变性**：如果未来 `ChatModule.get_config().name` 改了，
  MCP server name 会跟着变，所有 `mcp__chat_module__*` 前缀就会失效。改 module
  名称必须同步更新本 prompt 里的 6 处全名引用。
- **后端兜底是 `endswith` 匹配**：`chat_module.py:256` 用
  `tool_name.endswith("send_message_to_user_directly")` 检测，所以 prompt 教
  LLM 用全名调用不会破坏后端识别——兼容。

## 新人易踩的坑

- 给 Agent 添加新的触发场景（`WorkingSource` 枚举加值）时，必须在这个 prompt
  的"By Trigger Source"表格里加一行明确说明新场景的发送规则，否则 Agent 会
  fallback 到"不知道该不该发"而随机发送。
- 想"简化"prompt 把所有 `mcp__chat_module__send_message_to_user_directly`
  改回裸名 `send_message_to_user_directly`——会回到 2026-04-22 之前的"find
  tool fails → silent turn"bug。保留全名是刚性要求。
