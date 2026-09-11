---
code_file: tests/message_bus/test_unread_preview_truncation.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 为什么存在

钉住 B-23 / upstream #73：未读列表绝不给 agent 一条被静默截断的消息。用 #73 原文（三段、
约 560 字）断言整条渲染；超出 `UNREAD_PREVIEW_MAX_CHARS` 的行必须带截断声明（原长、展示长、
`read_history`），恰好等于预算的不标；分片行 `(part i/n)` 与截断声明共存；静态块那句
「未读已在 context」必须说明长消息会被截断。把 [[../../plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/message_bus_module]]
的渲染改回 `[:200]` 硬切，前四条变红。
