---
code_file: src/narranexus/contracts/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 公开 API 面的唯一入口

插件只允许 `from narranexus.contracts import ...`（宪章 8/11）。本文件导出 `_base` 的原语
（Disposable/DisposableStack/CancellationSignal/错误层级/Stability）并持有两张表：
`API_VERSIONS[kind]`（每个契约 kind 的整数版本，只增不减，破坏性改动必须 bump）与
`STABILITY[symbol]`（alpha/beta/stable）。批 0-5 全部 alpha，批 6 才标 stable
（spec §5.5 / §16.1）。`tests/nx_kernel/contracts/test_base.py` 钉住两表键集一致且全 alpha；
`tests/nx_kernel/test_package_layout.py` 钉住 import 本包不会带进 kernel/legacy/backend。
