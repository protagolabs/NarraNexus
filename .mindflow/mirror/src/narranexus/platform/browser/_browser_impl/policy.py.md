---
code_file: src/narranexus/platform/browser/_browser_impl/policy.py
last_verified: 2026-09-23
stub: false
---

# Origin decisions and scoped answers

普通 HTTP(S) 访问不属于权限模型。access、persistent_approval、access_approval_lifetime
字段已删除，旧数据只解析现有能力；仅含访问规则的 origin 以及访问 grant/denial 不进入模型。
无数据迁移、无兼容开关。上传、下载、高级脚本和历史权限保持独立；default_verdict
只提供仍支持的能力默认值。

Policy is the common decision function for configured origin rules and explicit
user answers. Configured deny wins over temporary and permanent approval paths.
Turn and conversation scope keys use separate namespaces; blank scope identifiers
cannot authorize unrelated callers. Scoped denials obey the selected lifetime
just as allows do. Persistent grants, denials and decision receipts cross the
backend/MCP process boundary through one JSON document.

Origins reject malformed ports and retain IPv6 brackets. Wildcard matching
requires a hostname boundary and never implicitly includes its bare domain.
full_cdp_access cannot be granted by a routine prompt. Session code treats
arbitrary page JavaScript as privileged because ordinary browsing cannot limit
the requests or navigation that script may initiate.

These rules gate explicit tools, not all browser network traffic. Upload and
download verdict fields are policy data, not an assertion that arbitrary scripts
have those effects intercepted. Current launches disable filesystem downloads.
