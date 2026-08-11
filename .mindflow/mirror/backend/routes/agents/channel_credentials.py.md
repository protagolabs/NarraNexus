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

`channel` 走 allowlist（PR-A 仅 discord）→ 未知 channel 在任何 db 查询前先 404，不能拿来探测未接线的 channel。
命中则用 `DiscordCredentialManager(db).get(agent_id)`：None→`{"bound": false}`，否则 `cred.to_raw_dict()`。
**绝不 log 密钥**——raw 字段只在 `to_raw_dict` 一处离开管理器，端点整体透传不碰单字段。
附带 `GET /{agent_id}/channels/name`（同两道门）供 seam 的 get_agent_name 用。
