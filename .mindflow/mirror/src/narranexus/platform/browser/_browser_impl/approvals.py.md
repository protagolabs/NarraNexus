---
code_file: src/narranexus/platform/browser/_browser_impl/approvals.py
last_verified: 2026-09-23
stub: false
---

# Approval decision semantics

普通网址访问已退出审批模型。仅 uploads、downloads、auto_review 可创建能力审批；
full_cdp_access 继续只能在设置中明确配置。access 请求在创建前拒绝。

This registry is pure decision logic used by the persistent store, not the
cross-process transport. Duplicate questions match agent, canonical origin,
capability, turn and conversation. Different scopes cannot silently share a
question whose answer would apply to the wrong caller.

Pending responses publish allowed_lifetimes. A turn or conversation choice is
offered only when that trusted identifier exists. Invalid decisions, invalid
lifetimes and missing scope do not consume the request. Routine prompts cannot
authorize full_cdp_access, and even an always answer cannot override configured
deny. A denial with turn or thread lifetime remains scoped; only always writes
a permanent origin rule.
