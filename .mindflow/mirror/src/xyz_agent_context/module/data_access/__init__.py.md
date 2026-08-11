---
code_file: src/xyz_agent_context/module/data_access/__init__.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11 (PR-A) — 导出 ChannelCredentialStore 家族

公共面新增 [[channel_store]] 的 `ChannelCredentialStore` 协议 + `ChannelDirectStore`/`ChannelHttpStore`
（以别名导出，避免与 AgentDataStore 的 DirectStore/HttpStore 撞名）+ 组合根 `get_channel_credential_store`。

## Why it exists

Public surface of the data-access seam (blueprint P0): the protocol
(`AgentDataStore`), both implementations and the composition root
(`get_agent_data_store`). MCP tools import from HERE, never from the
submodules — the seam's whole point is that a tool cannot tell (and must not
choose) which transport it got.
