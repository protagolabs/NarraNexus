---
code_file: src/xyz_agent_context/artifact/_artifact_impl/raw_access.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (方案 B 的后果修正) — 团队根纳入单文件守卫

单文件守卫原本只认 workspace 根：entry 直接坐在根上时拒绝子路径请求，**正是为了防止一个 token
顺着走该 agent 的其他文件**。团队 artifact 被要求住进团队目录后，entry 出现在一个该守卫不认识
的根上——于是一个团队 artifact 的 view-token 会把**团队目录里的任何文件**都提供出去，而这条路由
是**刻意绕过 JWT** 的（token 本身就是凭据）。

现在 `container_roots = {workspace_root} ∪ {team_shared_dir}`（team 从 artifact 行上取）。

# raw_access.py — resolve an artifact + sub-path to the file it serves

## Why it exists

Extracted 2026-07-21 from `backend/routes/artifacts/public.py::get_raw`,
which had grown into a fat handler mixing HTTP concerns with pointer/path
logic. The split: this module owns everything that is NOT HTTP (pointer
lookup, flat→nested workspace fallback, path-escape confinement, the
workspace-root single-file rule, media-type choice); the route keeps token
verification and response headers (CSP). Covered by
`tests/artifact/test_raw_access.py`.

## Rules (all realpath-based so symlinks cannot escape)

- Artifact root (dirname of entry) must stay inside
  `settings.base_working_path`.
- **Workspace-root single-file mode**: when the entry sits directly at the
  agent workspace root, sub-path requests are refused (the sibling tree would
  be the whole workspace — Bootstrap.md and every other artifact's files).
  The entry's own basename is tolerated as an alias of the entry.
- Sub-paths are realpath-confined to the artifact root.
- Media type: entry serves as the artifact's `kind`; assets are guessed via
  `mimetypes` (the kind describes the entry, not a sibling style.css).

## Error contract (the frontend depends on it)

- `ArtifactNotFound` (404): artifact missing, token/agent mismatch, path
  outside the root. Uniform 404 so probes can't map what exists.
- `ArtifactContentGone` (410): row exists but content is gone (empty
  file_path on legacy rows, entry/asset off-disk). 410 is the self-heal
  trigger in every renderer — never merge it into 404.

## Gotchas

- Escape attempts are logged (`path-escape blocked: ...`) before raising —
  keep the log, it is the only audit trail for probe attempts.
- `ResolvedRawFile.is_entry` drives the route's CSP choice (entry HTML gets
  the host-source CSP; assets get a generic strict one).
