---
code_file: src/xyz_agent_context/_skill_marketplace_impl/install_pipeline.py
last_verified: 2026-07-20
stub: false
---

# install_pipeline.py

The unified 7-step install/uninstall engine (spec §5). Every entrance — UI
zip/GitHub, URL, agent MCP tools, future marketplace source — converges here
so the scan gate, conflict/config migration, meta hash fields, audit trail
and auto-archive can never be skipped by taking a different door.

## Step order (and why)

Scan runs on the STAGED package (temp dir) before dependencies/compat/conflict
— a rejected package must never touch `skills/`, and the old version of a
skill must survive a rejected upgrade attempt. Same-version conflict returns
`already_installed` without touching disk. Replace captures the old
`env_config` first and merges it back after `install_from_dir` (which rmtree's
the target, destroying the old meta) — that is the "same-key config
auto-migration" from Phase 2 comment #3.

## Design decisions

- **Wraps SkillModule's public primitives** (`extract_skill_package` /
  `fetch_github_repo` / `install_from_dir` / `merge_skill_meta` /
  `read_skill_meta` / `parse_skill_package`) — added in the same commit
  precisely so this file needs no private access.
- **Disk is truth**: `_audit` and `_backup` swallow + log failures; a DB
  outage cannot fail or roll back a filesystem install. The reconciler
  (stage ⑥) heals the audit table later.
- `hash` = sha256 of the source zip (absent for github clones);
  `content_hash` = deterministic dir hash excluding `.skill_meta.json`
  (stable across meta rewrites — the reconciler's "modified" detector).
- Conflict lookup uses `sanitize_filename(incoming.name)` because
  `install_from_dir` lands the skill under the sanitized name.
- Compatibility check no-ops when `importlib.metadata` can't resolve the
  app version (editable installs) — availability over strictness for MVP.

## Gotcha

- `install_from_zip` re-extracts nothing: staging IS the only extraction;
  `install_from_dir` moves the staged tree. Both entrances clean their temp
  dir in `finally` (move makes `exists()` False on success).
