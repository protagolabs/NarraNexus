---
code_file: packages/narranexus-contracts/src/narranexus/contracts/skill.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2a）— `content.skills` 位的契约

一个 `SKILL.md` 目录 + `kind`（plugin/marketplace/workspace/builtin）。skill 模块像扫 workspace 技能一样
扫这些目录，并把 `kind` 记进 catalog，区分插件带来的技能与市场/手写技能。

## 2026-09-04 · services + host hooks (batch 3c.6)

`SkillWorkspace` Protocol — the members the platform drives a workspace through (list/get/read/merge meta, parse/extract/fetch, install_from_dir/from_github/install_skill, remove_skill, skills_dir); `SkillModule` implements it; `skills.workspaces` returns one.
