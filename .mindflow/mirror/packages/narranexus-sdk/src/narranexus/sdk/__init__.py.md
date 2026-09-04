---
code_file: packages/narranexus-sdk/src/narranexus/sdk/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2e）— Python SDK：插件作者的唯一门

re-export 契约类型、`Contribution`、`hookimpl`、`ServiceRef`、`PluginContext`——不是第二份拷贝，是稳定的门。
插件只 import 这里（或 `narranexus.contracts`），永不 import `narranexus.kernel` 内部。独立 PyPI 包
`narranexus-sdk` 是批 6 的事。
