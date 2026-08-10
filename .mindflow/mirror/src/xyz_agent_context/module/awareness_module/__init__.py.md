---
code_file: src/xyz_agent_context/module/awareness_module/__init__.py
last_verified: 2026-08-10
stub: false
---

# awareness_module/__init__.py — package surface

从 [[_awareness_writes]] re-export 共享写助手 `update_agent_profile_from_args` 及其身份笔记
字符串助手/常量（build/merge/IDENTITY_CHANGE_SECTION/MAX_IDENTITY_CHANGE_ENTRIES），供 seam 的
[[store]] DirectStore、backend 孪生路由 [[profile]]、以及测试 import **包**而非私有叶子（同
job_module 先例）。**故意不** re-export AwarenessModule 类（它由 MODULE_MAP 从 .awareness_module
引）——保持本 __init__ 只依赖 _awareness_writes 的轻依赖（repository + 惰性 message_bus），
不拖入模块重加载链。纯转发。
