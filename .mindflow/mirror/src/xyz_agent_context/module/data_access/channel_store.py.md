---
code_file: src/xyz_agent_context/module/data_access/channel_store.py
stub: false
last_verified: 2026-08-11
---
## 2026-08-11 (PR-H) — 写侧 seam：bind/unbind/test_connection

Protocol 加 `bind/unbind/test_connection`。unbind/test_connection **契约统一**(mgr.unbind / do_test_connection)；bind **异构**由 `_BIND_SERVICE`(模块,函数,takes=mgr|db)分派——discord/slack/telegram 收 mgr、narra 收 db。DirectStore 复刻路由 envelope(`{success,data:{unbound}}`)；HttpStore `_post_json` POST `/api/<ch>/<op>`、**never-raise 但返 `{success:False,error}`**(失败的 unbind 不是'已解绑')。lark 写整体排除(CLI-OAuth，另立)。已迁写：discord/slack/telegram/wechat(unbind)/narramessenger(bind)。


## 2026-08-10 (PR-A) — ChannelCredentialStore：per-channel 送信凭据的 MCP 取数 seam

[[store]] 的 AgentDataStore 搬的是叙事数据；这是**另一条** seam（Owner 拍板：独立 Protocol，不并入），
因为它返回的是 send 工具认证外部平台要用的**原始密钥**（Discord `bot_token`、Lark `app_secret`…），
性质与叙事数据不同、安全敏感，故自成 Protocol，只复用同一套 env-gated DirectStore/HttpStore 传输形态。

- **DirectStore**：`_manager_class(channel)` 懒解析到各 channel 的凭据管理器，调其 `get(agent_id)` 后
  用 `to_raw_dict()`（**不是** `to_public_dict()`——本 seam 存在的全部理由就是把密钥带过 HTTP 跳）序列化。
- **HttpStore**：GET owner-gated 后端端点 [[channel_credentials]]，转发身份头（同 [[factory]] current_identity_headers）。
  **绝不抛**：unreachable / 非 2xx / 未绑定 一律降级成 `None`，让 send 工具落到它既有的 "no_credential" 分支，
  不炸掉 agent 的一轮。`_seg` 按路径段百分号编码 LLM 可控 id（同 store.py 理由）。

channel→manager 用 `_MANAGER_REGISTRY`（channel → (模块路径, 类名, **读方法名**)，按名懒 import）集中登记，`SUPPORTED_CHANNELS`
由它派生（端点 allowlist 不会与 DirectStore 能解析的漂移）。读方法名**不统一**（discord/slack/telegram/wechat=`get`，lark=`get_credential`）
故入表第三字段，`_read_method_name` 取之、DirectStore 用 getattr 调用。加一个 channel = 注册表加一行 + 该 channel 凭据类加 `to_raw_dict`/`_cred_from_raw`，
seam / 端点 / allowlist 全跟随。已接入：discord/slack/telegram/wechat/narramessenger/lark/home_assistant（读侧；lark 读方法名 `get_credential`；HA 用薄 repository 适配器，raw=config_json）。
Protocol 除 `get_credential`/`get_agent_name` 外加 `get_agent_owner`（agents.created_by）——narra 的媒体发送 + CLI 工作区解析要它；DirectStore 读库、HttpStore 打 `/channels/owner`。**已知缺口（明写不藏）**：写/生命周期（bind/unbind/setup）尚未进本 Protocol，
`DirectStore.get_manager()` 是给 discord 写工具的**本地专用**便利、无 HttpStore 对应——所以云端写工具仍需本地 db，
`DB_PASSWORD` 要等写路径也迁移（另一 PR）才能从 mcp 摘掉。这是 #2「mcp 零 db 凭据」的收口条件。
