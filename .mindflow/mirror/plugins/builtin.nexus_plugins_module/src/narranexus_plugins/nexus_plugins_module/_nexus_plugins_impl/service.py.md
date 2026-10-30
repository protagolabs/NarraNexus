---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/service.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 云拒绝同时是草稿 I/O 的安全前提

草稿目录在 agent workspace 里，**所有经过 `_dev_dir` 的方法**（`scaffold` / `edit` / `validate` / `test` / `register` / `diff` / `publish_hint` / `wipe_draft`）加上 `list` 的 glob 都按路径读写（仅 `check_inside` 词法守卫）；`register` 还以 `LocalSource(dest, mode="link")` 链接式安装——已注册插件就是指向草稿的链接，上云时 kernel 会加载租户可重排目录里的代码。
在云上租户能把 workspace 内的目录换成指向别的租户的链接，这些 by-path 操作就会跨租户；本地 / 桌面是单租户，没有这个攻击者。
所以构造函数里的 `is_cloud_mode()` 拒绝不只是产品决定（云发标准插件集），也是这批 I/O 不改成 `confined_io` 的理由——
**去掉这个拒绝的 PR 必须先把草稿读写/删除迁到 `utils.fs.confined_io`**。代码注释与 [[confined_io]] 的例外清单写的是同一条。

## 2026-09-03（批 2f.1）— `SelfExtensionService`：plugin_* 动词的本体

docs → scaffold（`<workspace>/plugins/<id>`，用 CLI 同一 `scaffold`）→ edit（护栏 + `.plugin-changelog.jsonl`）
→ validate → test（报告落 `.test-report.json`）→ register（要当前树的绿报告；已注册则版本必须递增；link 安装、
`scope=agent:<id>`、`installed_by=agent:<id>`、注册后 **disabled**）→ activate（生成提案，用户在工场页决定；
hooks/routes/workers/tools/mcp 类必须先 agent 范围且 observe 过才能 global）→ `apply_decision`（工场 API 调：
批准则 enable + scope + 权限已确认）→ observe（状态/crash/UI 错误/审计条数）→ deactivate/rollback（两次转人工）
/diff/install/upgrade（都是提案）/publish_hint。每步进审计。云端构造即拒绝。不 import `backend`（铁律 #21）。
