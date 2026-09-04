---
code_file: src/narranexus/contracts/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `API_VERSIONS` 新增十个平台/内容 kind

`hook/route/table/worker/settings/tool/mcp_server/bundle/skill/theme` 各自一个契约模块（theme 在 `ui.py`），
全部 alpha。`contributes` 白名单就是这张表的键（spec §5.1）。

## 2026-09-03（批 1）— `API_VERSIONS` 新增 `agent_events` / `agent` / `services` / `ui`；`Namespace`

事件字典契约独立计版本（线上协议），与 `framework`（driver Protocol）分开 bump；`agent`（纵横模型）、`services`（`kernel.*` 位的宿主服务 Protocol）、`ui`（前端壳描述）各自一个 kind，全部 alpha。`Namespace` 是分组位（`model`/`agent`/`backend`/`ingress`/`content`）的契约符号：内核拥有、永不绑定，存在只为让 `kernel/plugins/slots.py` 里每个契约字符串都能 import 到（`test_every_kernel_slot_contract_symbol_resolves`）。

## 2026-09-03 — 公开 API 面的唯一入口

插件只允许 `from narranexus.contracts import ...`（宪章 8/11）。本文件导出 `_base` 的原语
（Disposable/DisposableStack/CancellationSignal/错误层级/Stability）并持有两张表：
`API_VERSIONS[kind]`（每个契约 kind 的整数版本，只增不减，破坏性改动必须 bump）与
`STABILITY[symbol]`（alpha/beta/stable）。批 0-5 全部 alpha，批 6 才标 stable
（spec §5.5 / §16.1）。`tests/nx_kernel/contracts/test_base.py` 钉住两表键集一致且全 alpha；
`tests/nx_kernel/test_package_layout.py` 钉住 import 本包不会带进 kernel/legacy/backend。
