---
code_file: backend/routes/billing.py
last_verified: 2026-07-02
stub: false
---

# billing.py — NetMind 计费/订阅代理路由（`/api/billing`）

## 为什么存在

D-1 决策：计费 API 走**后端代理**（避 CORS、统一持凭证、未来 key 落库），不让
前端直连 NetMind。本路由把前端持有的 NetMind `loginToken` 转发到 NetMind 计费
API，只加 HTTP 信封、cloud 门禁、错误映射。委托给 [[netmind_billing_client]]。
注册在 [[main]]，prefix `/api/billing`。

## 上下游

- 上游：前端 `api.getPlans()` / `api.getSubscription()`（[[api]]，经
  `X-Netmind-Token` 头带 loginToken）；[[NetmindAccountPanel]] 消费。
- 下游：[[netmind_billing_client]] → NetMind 计费域。

## 踩过的坑 / 设计决策

- **cloud 门禁用规范判定器** `utils.deployment_mode.is_cloud_mode()`（认
  `NARRANEXUS_DEPLOYMENT_MODE`），**不是** `providers.py::_is_cloud()`（只看
  sqlite、不认 env）——否则本地 cloud-smoke 打不开 gate、语义也不规范。
- **身份分层**：`/subscription` 先 `resolve_current_user_id`（本地身份门禁，挡
  未登录）再取 NetMind token。但本地 user_id 目前只做"是否登录"的存在性门禁，
  **不与 NetMind 账户做绑定校验**——授权边界委托给 NetMind（token 谁的就是谁的
  数据）。Phase 1 只读自己数据可接受；**加写操作（subscribe/cancel）前需在
  Phase 2/3 明确绑定或显式记录该边界**（安全审查 #3）。
- **错误映射**：`BillingAuthError`→401（前端据此重新走 NetMind 登录）、
  `BillingUpstreamError`→502（不把上游不可用伪装成用户凭证错）。客户端响应永远是
  固定信封/固定 detail，不泄漏上游 body/栈/token。
- **`/api/billing` 在 [[auth]] 的 `QUOTA_BYPASS_PREFIXES`**：超额用户正是最需要看
  "升级 Pro" 面板的人，不能被 402 挡在门外（安全审查 M-1）。
- **前端 401 特判**：billing 的 401（NetMind token 失效）**不得**触发全局
  `narranexus:auth-expired` 登出——见 [[api]] 的 `isBillingEndpoint` 跳过逻辑。
