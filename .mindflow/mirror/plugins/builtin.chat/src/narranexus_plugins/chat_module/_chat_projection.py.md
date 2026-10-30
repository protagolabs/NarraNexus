---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/_chat_projection.py
last_verified: 2026-10-30
stub: false
---

# _chat_projection.py — owner 面板从一条 chat memory 消息里看到什么

## 为什么存在

对话 tab（[[routes]] 的 simple-chat-history）和侧栏后台「有新回复」信号（[[_chat_latest]]）必须
对同一条消息给出同一个判断，否则信号会报 tab 里没有的行、或漏掉 tab 里有的行（2026-10-30 用
`events` 表近似的方案正因此被否）。所以投影只有一份：`project_message(msg, peer_scoped)`。

规则（从 routes 原样搬来）：peer scope（A2A / team）只留 `A2A_TEAM_SOURCES` 的行；非
`USER_FACING_SOURCES`（chat / manyfold 以外）只留 assistant 行；这类 assistant 行若带
`owner_notify_content` 则原文透出（peer scope 标成 activity），否则折叠成
`Background activity (<source>)` 并标 `message_type="activity"`。时间戳取
`meta_data.timestamp`，缺失时退回 `created_at`。

同时放两边都要的 `parse_timestamp`（多格式、去时区）、`parse_json_field`，以及表名常量
`CHAT_MEMORY_TABLE`（[[_chat_reads]] 也从这里导入，不再各写一份）。

## 依赖

只依赖 `narranexus.platform.schema.hook_schema.BUS_PRODUCED_SOURCES` 与 loguru，不引 backend，
MCP 侧（_chat_reads）可安全导入。
