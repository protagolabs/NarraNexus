---
code_file: src/narranexus/platform/services/skill_sync_service.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30（复审 2）— 对账里的哈希在线程里跑

`reconcile_workspace` 对每个技能用 `asyncio.to_thread(self._live_status, …)`：`backend/main.py` 周期性对账覆盖所有租户，一个租户的深目录树不能再阻塞后端事件循环。

## 2026-10-30 — 对账列表走受限路径

`reconcile_workspace` 的 `skills/` 与 `.disabled/` 列表、`reconcile_all` 的 base → user → agent 三级遍历全部用 `confined_io.list_dir` / `lstat`：租户把 `agent_x` 目录
换成指向别的租户 workspace 的链接，不会再被当作该 agent 的 workspace 去读取并记到攻击者的行上。

## 2026-08-04 — 对账有变更时刷新同伴发现行

`reconcile_workspace` 末尾，`any(stats.values())` 为真（磁盘上多了/少了技能）时调
[[agent_discovery_sync]]。对账改变的正是"这个 agent 能做什么"，而 capabilities 是
同伴搜索的匹配面，不重算名录就是陈的（P1 段02）。

# services/skill_sync_service.py — skill 对账器

Keeps `skill_installations` (audit DB) following the filesystem truth.
Users can always hand-edit `skills/` — so the DB is a FOLLOWER: this
service ONLY writes DB rows, never touches user files (tested explicitly:
a reconcile pass leaves the tree byte-identical).

## Drift matrix

disk-only → row added (source from `.skill_meta.json`, else `manual`);
DB-installed but gone from disk → `external_removed` (row kept for audit);
content hash ≠ recorded `content_hash` → `modified`; under `.disabled/` →
`disabled`; back on disk → `installed` again. Idempotent: pass 2 is a
no-op. `uninstalled`/`external_removed` rows are terminal-stable (not
re-flagged every pass).

## Wiring

Runs inside the backend lifespan (`backend/main.py`) as a single
fire-and-forget `create_task(run_forever())` — the lifespan does NOT run a
separate blocking startup pass and does NOT await the first reconcile
(scanning every workspace + hashing every installed skill grows with users;
blocking startup on it was the 2026-07-22 lifespan-latency fix). Instead
`run_forever` does its FIRST reconcile immediately inside the loop, then
sleeps `SKILL_SYNC_INTERVAL_SECONDS` (default 1800) between passes. Setting
the interval `<= 0` returns before the loop even starts, so it disables the
ENTIRE reconciler — no first pass and no periodic loop (tests opt out this
way). Cancelled on shutdown with a done-callback that logs unexpected exits
(fire-and-forget lesson #2). Lives in the backend process — both run modes
(run.sh / DMG sidecar) boot the same lifespan, iron rule #7.
`reconcile_all` walks the NESTED workspace layout `{user_id}/{agent_id}`
(see utils/workspace_paths.py `_LAYOUT`); if the layout flips again this
walk must follow.

Enable/disable via the UI routes does NOT write the audit table directly —
the reconciler is what converges those states (acceptable lag ≤ interval;
DB is audit-only).

## 2026-09-04 · services + host hooks (batch 3c.6)

Workspaces come from the `skills.workspaces` service, not `SkillModule` directly.
