---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/protocols.py
last_verified: 2026-07-29
stub: false
---
# contracts/protocols — 13 个组件 Protocol

「接口一次到位、实现分期长大」的载体;loop 只 import 本文件与 events。判据:加第二个实现时 diff 不碰既有类。LedgerView 只读协议实现读写分离(策略拿不到写账本的刀);CancellationSignal 结构化重声明平台 CancellationView,保住 L0 零平台依赖。
