---
code_file: src/xyz_agent_context/module/wechat_module/_wechat_credential_manager.py
stub: false
last_verified: 2026-08-11
---

## Why it exists

`channel_wechat_credentials` 表的 CRUD + `WeChatCredential` 数据类。密钥（bot_token）落库前 base64 编码、读出即解码（明文字段只给调用方、绝不 log）。`get(agent_id)` 读、`to_public_dict` 是面板用的脱敏视图（无密钥）。

## 2026-08-11 (PR-B..D) — 原始视图 + 反序列化助手（channel seam 接入）

新增 `to_raw_dict()`（含明文 bot_token，= `{**to_public_dict(), 密钥}`，与脱敏视图严格区分，只交给 [[channel_store]] seam 的 owner-gated 端点 [[channel_credentials]] 与 send 工具）+ 模块级 `_cred_from_raw(raw)` 逆转（datetime 经 ISO round-trip，复用 `_parse_dt`），供 HttpStore 重建与 DirectStore 相同的 dataclass 保 parity。
