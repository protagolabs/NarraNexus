---
code_file: src/xyz_agent_context/utils/workspace_paths.py
stub: false
last_verified: 2026-08-14
---

## 2026-08-14 — 新增 `resolve_agent_workspace_cwd`（channel-CLI 共享 CWD 解析）

lark_cli_client 与 narra_cli_client 各自维护的 `_resolve_agent_workspace_cwd` +
owner 缓存收敛为一份（PR#308 review Important-3）：owner 走 channel seam 的
`get_agent_owner`（direct-db 与零凭据部署行为一致）、空 owner **不入缓存**（re-bind
后可重解析）、mkdir -p 保证 CLI 有处可写、任何失败返回 None=继承父 CWD（只坏下载、
不坏发送）。`log_tag` 参数保留 `[lark-cli]`/`[narra-cli]` 排障前缀。seam import 在
函数内——本模块加载期仍零依赖。守卫测试在 test_lark_cli_cwd.py（共享缓存由 autouse
fixture 清理，防 lark/narra 测试互染）。

## 2026-08-10 (review 修正) — 新增 `turn_accessible_roots`

一个回合在**自己 workspace 之外**能碰到的根，收口到一处。两项性质不同：

- `bus_files` — **按 user 授予是设计如此**：bus 把附件 stage **一份**到 owner 的共享区，
  同 user 的每个接收方读的就是那一个路径。按 team 收窄会直接打断消息投递（验收 #6 明确
  包含 bus 附件路径）。
- `teams/{team_id}` — **只给本回合所属的那一个 team**。

**父目录 `_shared` 永不返回**：给了它等于用一个入口把该 owner 的每个 team 又放回来。

初版就是那样：每个回合都授予整棵 `_shared`，包括压根不属于任何 team 的一对一私聊回合。
这与本功能其余部分自相矛盾——[[registration.py]] 的 `_resolve_entry` 只放行本回合那一个
team，[[artifact_repository.py]] 的 `list_for_agent_context` 特意 join `team_members` 而非
按 owner，注释还写明按 owner 取就是 cross-team leak。确权层当时用的正是 owner 口径。

**「accessible」不是「readable」**：消费它的 confinement 层检查 `file_path` 与 shell 路径，
所以同一份授予同时管 Write / Edit / rm。

## 2026-07-20 — per-user shared-area helpers

Added `user_shared_root` / `bus_files_dir` / `team_shared_dir`, all rooted at
`{base}/{user_id}/_shared` — a SIBLING of each agent's own workspace dir, deliberately
not inside one. Because the per-user Executor bind-mounts the whole `{base}/{user_id}`
subtree, these dirs are Read-able by every same-user agent in both local and cloud
mode. This is what makes cross-agent file sharing on the bus work without copying into
each recipient's workspace (see [[_bus_attachment_impl]]).

## Why it exists

Single source of truth for an agent's on-disk workspace layout. The
layout `{base}/{agent_id}_{user_id}` used to be hardcoded as
`f"{agent_id}_{user_id}"` in ~11 places (step_3, bundle builder/importer/
skill_backup, bootstrap, skill_module, attachment_storage, the artifact subsystem (xyz_agent_context/artifact),
arena_provisioning, identity_migration). This module centralizes it so the
layout can change in ONE place.

## Layout switch

`_LAYOUT` selects the on-disk shape:
- `"flat"` — legacy `{agent_id}_{user_id}`.
- `"nested"` — `{user_id}/{agent_id}` (**current**). This is what lets a
  per-user Executor container bind-mount only `{base}/{user_id}` and thus
  see ONLY that user's agents — cross-user file isolation by mount, no uid
  tricks (the P2 plan, binding rule #20 data-plane).

Flipped flat→nested on 2026-06-17 together with the migration below. All
call sites route through `agent_workspace_relpath` / `agent_workspace_path`,
so the flip was a one-line change here.

## Migration (`migrate_flat_to_nested`)

One-off, idempotent, non-destructive (rename only; never overwrite/delete).
CLI: `scripts/data_migrations/migrate_workspace_layout.py` (dry-run default, `--apply`).

**Disambiguation gotcha (why it takes `known_user_ids`):** a flat dir
`agent_<hex>_<rest>` is ambiguous — `<rest>` could be the user_id directly,
OR the legacy `_user_` infix form (`agent_x_user_binliang` = user
`binliang`, not `user_binliang`). Dir names alone can't tell them apart, so
the migration resolves `<rest>` against the authoritative set of real user
ids from the DB `users` table. Dirs whose owner doesn't resolve are
reported as `unknown` and **left in place — never guessed** (avoids
creating bogus user dirs). Verified on real data 2026-06-17: 284 moved,
0 conflicts, 53 unknown orphans safely left.

## Reader fallback resolvers (avoid a DB rewrite)

The dir migration moves files, but DB columns that store a workspace path
WITH the prefix (notably `instance_artifacts.file_path`, base-relative like
`agent_x_user_y/work/o.html`) are NOT rewritten. Rather than a risky DB
migration (binding rule #6), READERS of existing data use:
- `resolve_existing_workspace(agent_id, user_id, base)` — the workspace dir
  that EXISTS, current layout first then legacy flat / `_user_` fallback.
- `resolve_workspace_relative_file(file_path, agent_id, user_id, base)` —
  resolves a stored base-relative-with-prefix path to a file that exists,
  swapping the prefix flat↔nested if needed.

Wired into every hardcoded flat site the nested flip would otherwise break
(binding rule #8 sweep): `artifacts/public.py`, `agents/artifacts.py`,
`agents/files.py`, `manyfold/files.py`, `auth.py` (workspace delete + the
THREE `bootstrap_active` checks: GET agents, update agent, create agent),
`common_tools_module.py` (artifact list display), `context_runtime.py`
(Bootstrap.md path → bootstrap_active gate), and `_social_mcp_tools.py`
(sub-agent workspace create). So both old (flat) and new (nested) rows
resolve — no DB rewrite, works through the transition forever.

**Why the bootstrap ones mattered:** `apply_bootstrap` writes `Bootstrap.md`
to the nested path, but `bootstrap_active` was checked at the flat path in
3 places → the gate read False → the new-agent greeting + "read Bootstrap.md
and introduce yourself" prompt silently vanished. The audit first missed
these (they key off `created_by`/`owner_user_id` + multiline joins).

## Gotchas

- `_LAYOUT` must be flipped to `"nested"` ONLY after the migration has run
  on a base, or running agents lose their workspace.
- Agent ids are `agent_<hex>` (single token, no internal `_`) — the parse
  relies on this.
- run.sh uses `BASE_WORKING_PATH=/data/workspaces`; the settings default is
  `~/.nexusagent/workspaces` — migrate whichever base a given deploy uses.
- deploy: to bind-mount per `{user_id}`, `workspaces` must be a host
  dir / volume-subpath, not an opaque named volume (deploy-repo change).
