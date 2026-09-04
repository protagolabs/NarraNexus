---
code_file: src/narranexus/contracts/skill.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a）— `content.skills` 位的契约

一个 `SKILL.md` 目录 + `kind`（plugin/marketplace/workspace/builtin）。skill 模块像扫 workspace 技能一样
扫这些目录，并把 `kind` 记进 catalog，区分插件带来的技能与市场/手写技能。
