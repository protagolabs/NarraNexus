---
code_file: src/narranexus/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 新包：插件平台的内核与公开契约（批 0）

`narranexus` 是插件平台专题（spec `docs/superpowers/specs/2026-09-03-plugin-platform-design.md`，
deploy 仓）落地的第一块：一个刻意很小的新顶层包，价值在**边界**而不在体量。
分层由 pyproject `[tool.importlinter]` 在 CI 强制：`contracts` 是叶子（不 import kernel/
legacy/backend），`kernel` 只看 contracts，`xyz_agent_context`（遗留包）两者都可 import。
D8 决定包名终态就是 `narranexus`；批 0-5 的新代码都落在这里，批 6 再把遗留包改名并入。
`__version__` 独立于应用版本（`pyproject` 的 1.x），它是契约/内核这层自己的 SemVer 起点。
