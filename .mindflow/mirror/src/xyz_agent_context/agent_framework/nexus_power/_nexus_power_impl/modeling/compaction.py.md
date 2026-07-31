---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/compaction.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — estimate_message_tokens 外借给输出钳制

同一个 `_CHARS_PER_TOKEN` 比例,供 loop 在构造请求时估输入大小。之所以不放在 loop:
账本的实测输入在**一个 turn 的第一步是 0**,而正是那一步的投影已经背着此前所有轮次,
所以必须有一个纯按消息估的数,和实测值取大。估算只用于**定大小**,计费永远走
provider 上报的 usage。

ToolResultPruner:确定性裁剪不动 LLM,尾部 keep_recent 保护,配对安全(占位仍是合法 tool 消息)。字符估算只用于省量目标,计费永远真实 usage(C3)。SummaryCompactor v1.5 座位:费用归用户、默认用户主模型、usage 单列(Owner 拍板);narrative 联动经事件日志解耦。
