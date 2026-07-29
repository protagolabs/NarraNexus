---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/mcp_channel.py
last_verified: 2026-07-29
stub: false
---
# tooling/mcp_channel — MCP client 通道(v1 核心)

mcp__{server}__{tool} 命名(三处子串依赖);并发连接、单 server 失败降级为缺席;add_servers=动态展开末端(尾部追加+generation 递增+同名先注册者赢);aclose 全量收割连接(孤儿连接事故类)。结果文本 isError 归一为错误型结果。
