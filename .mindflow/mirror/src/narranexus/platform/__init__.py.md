---
code_file: src/narranexus/platform/__init__.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— 平台层落点

「所有插件都需要且插件做不了」的东西：今天只有回合流水线 `platform.turn`。可 import 内核、契约与迁移中的
`xyz_agent_context` 域包；永不 import 插件。
