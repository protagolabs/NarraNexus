---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/session/event_log.py
last_verified: 2026-07-29
stub: false
---
# session/event_log — 两轨日志出口

event_to_row 是 NDJSON 与未来 nexus_events 行的同一形状(entry 即 schema)。Streaming(回传 sink)/File(本地真相落盘)/Null(测试显式选择)。日志是路过不是分叉:append 不许拖慢事件流。
