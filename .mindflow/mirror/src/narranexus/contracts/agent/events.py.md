---
code_file: src/narranexus/contracts/agent/events.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 十四个阶段钩子名（onWill/onDid × 七阶段）

以 `(params, firstresult)` 元组声明而不是直接 `HookSpec`，因为契约包是叶子不能 import 内核；
`tests/nx_kernel/contracts/test_agent_contracts.py` 证明 `HookRegistry` 能逐个 declare。
`onWill*` 可返回替换输入（firstresult），`onDid*` 只观察。接线在批 3。
