---
code_file: src/xyz_agent_context/utils/db/db_backend_sqlite.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — aiosqlite worker 线程不许死在"投递结果"这一步

模块导入时把 `aiosqlite.core._connection_worker_thread` 换成
`_resilient_connection_worker_thread`。上游的写法是：语句执行完用
`future.get_loop().call_soon_threadsafe(...)` 把结果交回去；如果发起该语句的
loop 已经关闭，这一句抛 RuntimeError，而它的 `except` 分支**又调了同一个方法**，
再抛一次，没人接——**worker 线程就此静默死亡，手里还攥着一条打开的 sqlite3
连接**。

之后没有任何东西能回收它：文件句柄和那笔被放弃的语句持有的锁一直在，后续每个
writer 都要走满 10 次重试 x 30 秒 busy_timeout（~5 分钟）才等到
"database is locked"。触发条件一点都不罕见——任何摸数据库的
fire-and-forget 任务（登录时的 `schedule_user_no_quota_rearm` 只是其中之一）
都可能在自己的 loop 消失时还在飞，而 MCP 容器**设计上**就是每个 module 一个
短命的线程 loop。

处理原则：**丢结果可以，死线程不行**。等待它的协程已经随 loop 一起没了，结果
本来就无人接收；但这条线程是唯一能关掉这条连接的人。这是项目事故清单第 2 条
（第三方的 fire-and-forget 同样是雷）落在 aiosqlite 上的具体一例，替换范围刻意
收窄：只换"投递"那一步，队列协议和 STOP 哨兵仍是上游的。

配套修复见 [[db_factory.py]]（驱逐时真的关连接）。守卫见
`tests/utils/db/test_sqlite_orphaned_connections.py`，其中一条专门断言这个
猴补丁**确实装上了**——它是一个 import 副作用，重构时很容易丢，而丢了不会报错，
只会让每次碰撞多花 5 分钟。

顺带记下一个没在本次改动的放大器：`_retry_write` 的 10 次重试与
`PRAGMA busy_timeout=30000` 是**相乘**的（每次重试都在 SQLite 里等满 30 秒），
最坏情况一次写阻塞 ~5 分钟。作者多半以为"10 次快速重试"。

## 2026-08-07 — `get_by_ids` 支持 `fields`

与 MySQL 后端同形，标识符用双引号。见 [[database.py]]。

顺带记一个已经咬过人的行为（本次没改，只是写明）：`get_by_ids` 为了**保持输入
顺序**，会给查不到的 id **补 `None` 占位**（`result_map.get(id_val)`）。调用方直接
对返回行下标就会 `TypeError`，而如果调用方外面套了 advisory except，整批会被静默
吞掉。


## 2026-05-22 — initialize() guards the parent dir (clear error, not cryptic)

Before `aiosqlite.connect`, `initialize()` ensures the DB's parent dir
(`~/.narranexus`) is writable via `fs_safety.ensure_writable_dir` (which repairs
perms on a dir we own). Unlike logs, the DB CANNOT be diverted to a temp dir —
it's the user's data — so if the dir is foreign-owned (e.g. carried over by
Migration Assistant) it raises a clear `RuntimeError` naming the dir + the exact
`sudo chown` fix, instead of aiosqlite's cryptic `unable to open database file`
that (pre readiness-gate) killed the proxy/backend on startup. `:memory:` skipped.

# db_backend_sqlite.py

`SQLiteBackend` — the `DatabaseBackend` implementation for local/desktop deployments, using `aiosqlite` with WAL mode and a serializing write lock.

## Why it exists

The Tauri desktop migration needed a file-based database that runs without a server process. `db_backend_sqlite.py` provides the concrete SQLite driver that `AsyncDatabaseClient` delegates to when `DATABASE_URL` starts with `sqlite://`. It wraps `aiosqlite` (async SQLite via a thread pool) and adds three layers of application-level concerns that SQLite itself doesn't handle the same way MySQL does: write serialization, automatic timestamp parsing, and value serialization for composite Python types.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` when no `SQLITE_PROXY_URL` is set and the URL scheme is `sqlite`.

**Implements:** `DatabaseBackend` (from `db_backend.py`), so `AsyncDatabaseClient` uses it transparently.

**Depends on:** `aiosqlite`, `db_backend.py` (ABC), stdlib `asyncio` and `json`.

## Design decisions

**Single long-lived connection, not a pool.** SQLite is a file — there is no network round-trip to amortize. A single `aiosqlite.Connection` is opened at `initialize()` and kept for the backend's lifetime. Connection overhead is negligible compared to the overhead of opening a new file handle per query.

**`asyncio.Lock` for write serialization.** SQLite allows only one writer at a time within a process. Rather than relying on SQLite's retry timeout, the backend holds a write lock before executing any `INSERT`, `UPDATE`, `DELETE`, or `UPSERT`. Reads (`SELECT`) bypass the lock to maximize concurrency under WAL mode.

**WAL journal mode.** `PRAGMA journal_mode=WAL` is set at `initialize()`. WAL allows multiple concurrent readers even while a write transaction is in progress, which is essential for the agent pipeline where many coroutines read context data while background services write module state.

**Automatic timestamp parsing in `_auto_parse_row`.** SQLite stores all datetime values as TEXT. Rather than forcing every caller to parse timestamps, the backend converts columns whose names match known suffixes (e.g., `_at`, `_time`, `created_at`) to Python `datetime` objects when rows are returned. The detection is suffix-based, not universal, to avoid false positives on non-timestamp TEXT columns.

**JSON/dict/list serialized to strings.** Python dicts and lists passed to `insert` or `update` are serialized to JSON strings before storage. On read, the backend does not auto-deserialize JSON (unlike timestamps) — callers that store JSON must `json.loads()` the returned string themselves. This asymmetry is intentional: timestamp conversion is safe and universal, but auto-deserializing every TEXT column that looks like JSON would be error-prone.

**`upsert` uses `INSERT OR REPLACE`.** SQLite's `INSERT OR REPLACE` deletes the conflicting row and re-inserts, which resets auto-increment IDs and triggers `ON DELETE` cascades if any exist. An alternative `ON CONFLICT DO UPDATE` approach was not chosen here; callers that care about preserving the row ID should check whether this matters for their table.

## Gotchas

**Timestamp parsing by suffix, not by type.** If a new TEXT column is added whose name ends in `_at` but does not contain a datetime value, `_auto_parse_row` will attempt to parse it and either return a garbled `datetime` or fall back to the raw string. Avoid naming non-timestamp columns with timestamp suffixes.

**Write lock is per-backend-instance, not per-file.** If two `SQLiteBackend` instances are created pointing at the same file path (which should not happen in production because `db_factory.py` enforces a singleton, but can happen in tests), their write locks are independent and they will race. The symptom is `sqlite3.OperationalError: database is locked`. Use the proxy backend in any multi-process setup.

**WAL files accumulate after a crash.** If the process is killed mid-write, the `-wal` and `-shm` sidecar files remain on disk. SQLite handles recovery automatically on the next open, but the presence of these files can confuse backup scripts that copy only the main `.db` file.

**New-contributor trap.** `aiosqlite` runs SQLite in a thread pool under the hood. Calling a synchronous SQLite operation on the `aiosqlite` connection object directly (without `await`) will block the event loop thread. Always use `async with conn.execute(...)` patterns.
