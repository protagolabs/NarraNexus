---
code_file: src/narranexus/platform/utils/fs/fs_safety.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 搬入 `utils/fs/`

从 `utils/` 根搬入文件系统安全分组（意图见 [[utils/fs/__init__]]），纯搬迁、行为不变。调用方写 `from narranexus.platform.utils.fs import fs_safety` / `narranexus.platform.utils.fs.fs_safety`。

# fs/fs_safety.py — make ~/.narranexus dirs usable, repair-not-workaround

## Why it exists

A stale/foreign `~/.narranexus` (created by root on an earlier run, or carried
over from another Mac by Migration Assistant with that machine's numeric uid)
is unwritable for the current account → it silently killed the DB and logging on
startup → the desktop app could only show "Connection failed".

Shared by **both** consumers so the policy is identical for logs and the DB
(DRY — was duplicated in `logging/_setup.py`):
- `probe_writable(d)` — real touch test (`mkdir(exist_ok=True)` lies on an
  existing-but-unwritable dir).
- `chmod_repair_owned(target)` — adds `u+rwx` to dirs WE own under `$HOME`;
  **never** touches foreign-owned dirs (those need `sudo chown`) and never
  leaves `$HOME` (so it can't touch `/` or `/Users`).
- `ensure_writable_dir(d)` — probe → repair-if-owned → re-probe. False ⇒ foreign
  ownership / read-only mount, caller must surface it.
- `chown_hint(path)` / `narra_root_of(path)` — the exact `sudo chown -R <user>
  ~/.narranexus` to show the user.

## Consumers
- `logging/_setup.py::_ensure_writable_log_dir` — logs can divert to a temp dir
  if the real dir is foreign-owned (logs are disposable).
- `db_backend_sqlite.py::initialize` — the DB CANNOT divert (user data), so on
  `ensure_writable_dir == False` it raises a clear error with `chown_hint`.
