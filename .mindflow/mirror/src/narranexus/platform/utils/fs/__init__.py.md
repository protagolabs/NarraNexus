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

- `confined_io.py` — fd 级受限 I/O（见 [[confined_io]]）：宿主读写/删除租户可重排目录的唯一方式。
- `workspace_paths.py` — workspace / team 共享目录 / bus 目录的布局与 id 段校验（**构造**路径，不执行 I/O）。
- `file_safety.py` — 文件名、zip 成员路径、上传大小的校验（**校验**输入，不执行 I/O）。注意 `ensure_within_directory`
  返回 `resolve()` 过的路径，不能再拿去做受限 I/O——要受限 I/O 用词法路径交给 `confined_io`。
- `fs_safety.py` — 让平台自有的 `~/.narranexus` 目录可写（修复而非绕过），日志与 SQLite 共用。

2026-10-30 起四个模块都在这里（后三个由独立的机械 PR 从 `utils/` 根搬入，行为不变）。

`__init__.py` 不做 re-export：调用方显式写 `from narranexus.platform.utils.fs import <模块>`，模块名就是它的契约。
