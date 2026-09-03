---
code_file: frontend/src/platform/registries/registry.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 前端注册表的唯一形状（对应内核 `Registry[T]`）

插件平台批 1。与 Python 侧同语义：注册顺序即列表顺序（壳先、插件后）、重名默认报错
（`RegistryConflictError`）除非 `replace`、`register` 返回撤销函数、`freeze` 关闭注册。
额外两样是 React 需要的：`subscribe` 让组件在插件晚于首帧注册时重渲染，`snapshot()` 在两次变更之间
返回同一引用（`useSyncExternalStore` 的要求，否则无限重渲染）。
