---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_credential_manager.py
stub: false
last_verified: 2026-08-11
---

## Why it exists

`channel_narramessenger_credentials` 表的 CRUD + `NarramessengerCredential` 数据类。**两个密钥**：`bearer_token`（控制面）+ `matrix_access_token`（消息面 Matrix），均 base64 编码落库、读出即解码。`get(agent_id)` 读、`upsert(cred)` 整对象写、另有 `update_since_token`/`update_device_id`/`update_owner` 高频窄写。`to_public_dict` 脱敏（去两密钥，since_token 降成 bool）。

## 2026-08-11 (PR-E) — 原始视图 + 反序列化助手（channel seam 接入）

新增**显式** `to_raw_dict()`（**不能**用 `{**to_public_dict()}`，因脱敏视图有损：去两密钥、since_token 降成 bool）——含两密钥 + 完整 `matrix_since_token`，只交给 [[channel_store]] seam 的 owner-gated 端点 [[channel_credentials]] + send/CLI 工具。配 `_cred_from_raw(raw)` 逆转（全字段，datetime 经 ISO round-trip）。
