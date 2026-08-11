---
code_file: src/xyz_agent_context/module/data_access/channel_store.py
stub: false
last_verified: 2026-08-10
---

## 2026-08-10 (PR-A) — ChannelCredentialStore：per-channel 送信凭据的 MCP 取数 seam

[[store]] 的 AgentDataStore 搬的是叙事数据；这是**另一条** seam（Owner 拍板：独立 Protocol，不并入），
因为它返回的是 send 工具认证外部平台要用的**原始密钥**（Discord `bot_token`、Lark `app_secret`…），
性质与叙事数据不同、安全敏感，故自成 Protocol，只复用同一套 env-gated DirectStore/HttpStore 传输形态。

- **DirectStore**：`_manager_class(channel)` 懒解析到各 channel 的凭据管理器，调其 `get(agent_id)` 后
  用 `to_raw_dict()`（**不是** `to_public_dict()`——本 seam 存在的全部理由就是把密钥带过 HTTP 跳）序列化。
- **HttpStore**：GET owner-gated 后端端点 [[channel_credentials]]，转发身份头（同 [[factory]] current_identity_headers）。
  **绝不抛**：unreachable / 非 2xx / 未绑定 一律降级成 `None`，让 send 工具落到它既有的 "no_credential" 分支，
  不炸掉 agent 的一轮。`_seg` 按路径段百分号编码 LLM 可控 id（同 store.py 理由）。

PR-A 只把 `discord` 接进 `_manager_class`；后续每个 channel（slack/telegram/wechat/narramessenger/lark）
各自一 PR 加一行、不动 seam。**已知缺口（明写不藏）**：写/生命周期（bind/unbind/setup）尚未进本 Protocol，
`DirectStore.get_manager()` 是给 discord 写工具的**本地专用**便利、无 HttpStore 对应——所以云端写工具仍需本地 db，
`DB_PASSWORD` 要等写路径也迁移（另一 PR）才能从 mcp 摘掉。这是 #2「mcp 零 db 凭据」的收口条件。
