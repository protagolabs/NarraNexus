---
code_file: src/narranexus/platform/utils/fs/__init__.py
last_verified: 2026-10-30
stub: false
---

# utils/fs — 文件系统安全分组

## 为什么存在

PR #433 review（I3）：`utils/` 根目录已是 30+ 个平铺文件，`confined_io.py` 又是其中第 4 个
路径安全相关模块（`file_safety.py` 校验文件名、`fs_safety.py`、`workspace_paths.py` 构造路径）。
平铺导致"哪个 helper 什么时候不能用"只能靠散落在各调用点的注释来说明。这个子包定下方向：
**凡是决定宿主代码如何读写/删除租户可重排目录的原语，放这里**，不再堆到 `utils/` 根上。

## 现状

- `confined_io.py` — fd 级受限 I/O（见 [[confined_io]]）。
- `file_safety.py` / `fs_safety.py` / `workspace_paths.py` 仍在 `utils/` 根，按搬迁 SOP（铁律 #24）
  由独立的机械 PR 收进来；安全修复 PR 里不混入无关搬迁。

`__init__.py` 不做 re-export：调用方显式写 `from narranexus.platform.utils.fs import confined_io`，
模块名就是它的契约。
