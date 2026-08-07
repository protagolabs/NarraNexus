---
code_file: src/xyz_agent_context/artifact/_artifact_impl/registration.py
last_verified: 2026-08-07
stub: false
---

## 2026-08-07 — team 归属、共享目录可注册（缺口 T6）、归因历史

**归属**：`register_artifact` 增加 `team_id`（None = 私有）。它来自服务端身份 header，
**不是**模型参数——见 [[artifact_tool.py]]。

**放开路径（缺口 T6）**：`_resolve_entry` 此前把 entry 硬限制在 agent 自己 workspace 内，
而共享目录按设计是每个 agent workspace 的 **sibling**（谁都不拥有它）→ 放进共享目录的文件
**永远无法注册成 artifact**。「共享目录里的东西不能变成团队可见产出」，那这个共享目录只是
半个功能。现在额外允许**本回合所属那个 team** 的目录：兄弟 team 的目录仍然越界；且**相对
路径依旧只相对自己 workspace 解析**，团队目录只能用绝对路径抵达，不会把相对路径悄悄重定基
到 agent 没有指名的根上。

**归因历史**：三条写入路径（新建 / target 重注册 / 同 entry 去重）各追加一行
`instance_artifact_history`。`_record_history` **永不抛异常**——此刻 artifact 本身已经正确，
让一条日志失败去毁掉一次成功注册，是拿 agent 的真实工作换记账；缺行只是历史降级，抛异常
才是功能降级。

## 2026-07-23 — agent-scoped re-register dedup

New-artifact path (no target_artifact_id) with `session_id=None` now checks
for an existing PINNED row with the same (agent_id, file_path, kind) and, if
found, updates that row's pointer/title and returns its id instead of
inserting. Rationale: the LLM tool never knows a session_id, so every
re-register of the same entry file minted another pinned tab that lived
forever (prod: 2× "Welcome to NarraNexus"; dev: 3× briefing pages).
Session-scoped registrations keep the old semantics. Kind mismatch on the
same path falls through to a new row. Pre-fix rows are cleaned once by
[[cleanup_duplicate_pinned_artifacts.py]].

## 2026-07-22 — application/x-url added to ALL_KINDS

`URL_ARTIFACT_KIND` ("application/x-url") joined the whitelist so URL tabs
register through the same pointer path as everything else. Their entry file is
a JSON doc written by [[url_artifact.py]] before registration; no other change
to this module.
## 2026-07-21 — moved out of common_tools_module (was artifact_runner.py)

This file is the old
`module/common_tools_module/_common_tools_impl/artifact_runner.py`, promoted
into the dedicated `xyz_agent_context/artifact/` package. Logic is unchanged;
the exception classes moved to sibling [[errors.py]], and `_workspace_root`
became public `workspace_root` (heal shares it). History below is inherited
from the artifact_runner mirror.

# registration.py — pointer registration for artifacts

## Why it exists

The agent produces visual deliverables (ECharts JSON, HTML apps, CSV, Markdown,
images, PDFs) by **writing files into its own workspace** — that is its natural
working mode, and it lets a deliverable be multi-file (an entry `index.html`
plus `style.css`, `app.js`, `data.json`, images).

`register_artifact` is the bridge that makes such a workspace file *visible to
the user*. It does not write, copy, or move anything — it validates the entry
path, sizes the artifact root directory, sanity-caps it against
`MAX_ARTIFACT_BYTES`, and writes/updates one `instance_artifacts` row. Content
stays in the workspace; the backend serves it straight off disk.

## The model

- **artifact = entry file + its directory.** `artifact_root = dirname(entry)`.
  The whole root directory is served, so the entry HTML can reference siblings.
- The entry may sit anywhere inside the workspace — including the workspace
  root (single-file mode: size counts only the entry, and the serving layer
  refuses sibling requests so other workspace files stay private; see
  [[raw_access.py]]). Sibling-asset support is opt-in by putting the entry in
  a dedicated subdirectory.
- `target_artifact_id` re-registers onto an existing row (overwrites the
  pointer + title/description in place). Kind must match.

## Upstream / Downstream

- **Called by**: `ArtifactService.register` only (MCP tool, manual-register
  route, heal, and bootstrap all arrive through the service).
- **Depends on**: `ArtifactRepository` (DB I/O), `settings.base_working_path`
  (workspace root), `Artifact` / `ArtifactKind` / `CreateArtifactToolResult`.
- **Deliberately does not depend on**: agent_runtime, NarrativeService, any
  Module — it is a generic subsystem, not scenario-bound.

## Design decisions

- **`realpath` for the path-escape check.** Resolves symlinks: a
  workspace-interior symlink pointing at `/etc/passwd` is still rejected by the
  `startswith(workspace + os.sep)` test. `abspath` alone is not enough.

- **`size_bytes` is the recursive root directory size** (entry-file size only
  in workspace-root single-file mode) — stored for UI / debugging; nothing
  enforces a budget against it.

- **`MAX_ARTIFACT_BYTES` (25 MB) caps a single artifact** as a runaway guard.
  No per-user aggregate cap (removed 2026-05-19) — the agent's workspace
  already bounds disk usage, and the user owns the workspace.

- **No filesystem writes at all.** The only side effect is the DB row.

- **Office extensions override the caller's kind** (2026-07-13): entries
  ending in .pptx/.docx/.xlsx are forced to `OFFICE_LIVE_KIND` (imported from
  `utils/office_watch`, single source) — enables office-as-artifact and
  prevents registering a pptx as text/html.

## Gotchas

- `entry_path` may be absolute or workspace-relative — `_resolve_entry` joins
  relative paths against the workspace root before `realpath`.

- The artifact content is **live**: it points at the agent's real file. If the
  agent later edits or deletes the file/folder, the artifact changes or 410s.
  This is intentional (the whole point of the pointer model).

- `settings.base_working_path` is read at call time, not cached at import — so
  tests can monkeypatch it.

- The DB `file_path` is relative to `base_working_path`, so moving the
  workspace only needs a settings change; stored paths still resolve.

## Inherited history (from artifact_runner.py.md)

- **2026-05-19 — per-user quotas removed.** The per-user count/bytes quotas,
  `_enforce_quota`, `ArtifactQuotaExceeded`, and the repo quota helpers are
  gone. `MAX_ARTIFACT_BYTES` stays as the only cap.
- **2026-05-14-r3 — "must be in subdirectory" hard rule dropped.** With
  `delete_source`/rmtree gone, workspace-root entries became legal; exposure
  is prevented by soft-degrading at the serving layer instead.
- **2026-05-14 — rewritten for the pointer model.**
  The old copy/version model (create_text_artifact / upload_binary_artifact /
  version rows) collapsed into the single `register_artifact`.
