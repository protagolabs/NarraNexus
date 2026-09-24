---
code_file: src/narranexus/platform/browser/_browser_impl/policy_store.py
last_verified: 2026-09-23
stub: false
---

# Owner-managed browser permissions

公开视图与修改接口只包含 full_cdp_access；不再显示或接受 access 和 scoped_access。
旧的访问允许、询问、禁用及临时授权不会构成列表项，读取只依赖受支持的脚本字段。

Settings change one origin/capability rule against fresh storage using the same
binary JSON compare-and-swap boundary as approval resolution. Concurrent edits
and approval answers cannot replace each other's unrelated grants. Existing
configuration and replay receipts are preserved; no schema change is required.

The public view exposes full_cdp_access only. Unsupported upload/download,
history and auto-review configuration is neither advertised nor editable here.
Full CDP cannot use ask: owner settings deliberately allow or deny it. Origin
inputs reject credentials and paths instead of silently widening a path-specific
request to an entire origin. Ordinary browsing has no policy gate.

Changing a rule removes matching scoped answers and tombstones existing pending
prompts atomically with the policy write. Revocation is an explicit deny, so an
old or concurrent routine approval cannot re-enable a privileged capability.
