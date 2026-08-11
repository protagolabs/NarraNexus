---
code_file: src/xyz_agent_context/module/chat_module/__init__.py
last_verified: 2026-08-10
stub: false
---

# chat_module/__init__.py — package surface

从 [[_chat_reads]] re-export `fetch_chat_history`，供 seam 的 [[store]] DirectStore、
backend 孪生路由 [[chat_history]]、测试 import 包而非私有叶子（同 job/awareness 先例）。
故意不 re-export ChatModule 类（MODULE_MAP 从 .chat_module 引，避 init-order 耦合）。纯转发。
