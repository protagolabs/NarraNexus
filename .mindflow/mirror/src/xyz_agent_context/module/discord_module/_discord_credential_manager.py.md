---
code_file: src/xyz_agent_context/module/discord_module/_discord_credential_manager.py
stub: false
last_verified: 2026-08-11
---

## Why it exists

`channel_discord_credentials` 表的 CRUD + `DiscordCredential` 数据类。bot_token 落库前 base64 编码、
读出即解码（`bot_token` 字段是**明文**，只给调用方用、绝不 log）。`get_public`/`to_public_dict` 是给
面板路由的**脱敏**视图（无 token）。

## 2026-08-11 (PR-A) — 原始视图 + 反序列化助手（channel seam 接入）

新增 `to_raw_dict()`：含明文 `bot_token` 的**完整**序列化，与 `to_public_dict` 严格区分——只交给
[[channel_store]] seam 的 owner-gated 端点 [[channel_credentials]] 和需要认证 Discord 的 send 工具。
配套 `_cred_from_raw(raw)` 是其**逆**：让 HttpStore 拿到的 raw dict 重建出与 DirectStore 相同的
`DiscordCredential`（datetime 经 ISO 串 round-trip）。这两个是 seam 跨 HTTP 跳保 parity 的工具边界助手。
