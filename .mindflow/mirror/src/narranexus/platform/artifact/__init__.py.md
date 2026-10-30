---
code_file: src/narranexus/platform/artifact/__init__.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 导出 `confined_stat`

office-watch 版本轮询（backend `office_watch/proxy.py`）需要在受限 fd 上取 size/mtime；公共口导出，不让路由直捅 `_impl`。

# artifact/__init__.py — public surface of the artifact subsystem

## Why it exists

Artifact business logic used to live inside `common_tools_module/_common_tools_impl/`
(a Module's private impl) while three non-Module consumers — the HTTP routes,
bootstrap provisioning, and the heal endpoint — reached into it directly,
violating the "`_*_impl/` is never re-exported" rule. This package promotes
artifacts to a first-class core subsystem with one public seam.

Everything a consumer may touch is re-exported here: `ArtifactService`, the
`ArtifactError` hierarchy, `ResolvedRawFile`, and the `ALL_KINDS` /
`MAX_ARTIFACT_BYTES` constants. `_artifact_impl/` stays private.

## Upstream / Downstream

Consumers: `artifact_tool.py` (MCP tool), `backend/routes/agents/artifacts.py`,
`backend/routes/artifacts/public.py`, `bootstrap/profiles.py` (welcome
artifact). All import from this package, never from `_artifact_impl`.

## 2026-08-20 — 公共口新增三个出口

ArtifactEditConflict(409 编辑冲突)、inject_edit_bridge(raw 路由的
html 编辑桥注入)、office_lock_present(~$ 桌面锁检测)——路由层一律
经包公共口取,不直捅 _artifact_impl。
