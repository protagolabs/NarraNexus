---
code_file: src/xyz_agent_context/agent_framework/nexus_loop/contracts/tooling.py
last_verified: 2026-07-29
stub: false
---
# contracts/tooling — 工具面契约与标签工具

description 跟 ToolSpec 走(单一事实源防 prompt 漂移)。ToolAnnotations.marker_only=标签工具:调用即信号,dispatcher 短路执行,语义全在事件流(参数流式=用户看到的回复,投递归事件消费方)。deny/失败都是错误型 ToolResult,永不以异常穿透循环。
