---
code_file: backend/routes/agents/channel_credentials.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11 (PR-A) — 原始 channel 凭据的 HTTP 孪生端点（blueprint P2）

[[channel_store]] 的 HttpStore 打的后端端点，挂在 `/api/agents`。全库**唯一**返回原始 channel 密钥的路由，
故上**两道**门，缺一不可：

1. **service-caller 门** `_require_service_caller`：只接受 nx-agent（nx-service）bearer（复用 [[auth]] `_is_nx_service_bearer`
   查前缀；真正验签是 auth_middleware 干的、已 fail-closed）。普通用户 session JWT 即便 own 该 agent 也拒（403）——
   面板故意 mask 凭据，若允许浏览器 session 经 API 把原始 token 拉回来，等于把那层重新敞开（XSS 可批量外泄 bot token）。
2. **owner 门** [[_ownership]] `assert_owned`：token 证明的 user_id 必须 own 该 agent（挡跨租户窃取）。

`channel` 走 [[channel_store]] 的 `SUPPORTED_CHANNELS`（注册表派生）→ 未知 channel 在任何查询前先 404，
不能拿来探测未接线的 channel。命中则**委托 seam 的 `ChannelDirectStore().get_credential(channel, agent_id)`**
（不再硬编码某个 manager）——HTTP 孪生与进程内路径共用**同一份** db 访问，加 channel 只在 channel_store 注册表加一行、
本文件零改。None→`{"bound": false}`，否则透传 raw dict。**绝不 log 密钥**（raw 只在 `to_raw_dict` 一处离开管理器）。
附带 `GET /{agent_id}/channels/name`（同两道门）委托 seam 的 get_agent_name。
