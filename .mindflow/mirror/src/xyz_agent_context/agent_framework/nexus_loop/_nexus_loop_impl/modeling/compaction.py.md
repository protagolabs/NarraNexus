---
code_file: src/xyz_agent_context/agent_framework/nexus_loop/_nexus_loop_impl/modeling/compaction.py
last_verified: 2026-07-29
stub: false
---
# modeling/compaction — 压缩策略(v1 在场)

ToolResultPruner:确定性裁剪不动 LLM,尾部 keep_recent 保护,配对安全(占位仍是合法 tool 消息)。字符估算只用于省量目标,计费永远真实 usage(C3)。SummaryCompactor v1.5 座位:费用归用户、默认用户主模型、usage 单列(Owner 拍板);narrative 联动经事件日志解耦。
