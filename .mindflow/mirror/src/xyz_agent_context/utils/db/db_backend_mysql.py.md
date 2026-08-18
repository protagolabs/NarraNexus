---
code_file: src/xyz_agent_context/utils/db/db_backend_mysql.py
last_verified: 2026-08-17
stub: false
---

# db_backend_mysql.py

`MySQLBackend` — the `DatabaseBackend` implementation for cloud/server deployments, using an `aiomysql` connection pool.

## Why it exists

When the database layer was refactored to support pluggable backends, the MySQL-specific driver code (pool management, `%s` placeholders, backtick quoting, `ON DUPLICATE KEY UPDATE`) was extracted from `AsyncDatabaseClient` into `MySQLBackend`. This allows `AsyncDatabaseClient` to stay dialect-agnostic and lets `db_factory.py` select the backend based solely on the URL scheme. `MySQLBackend` is the backend for all production cloud deployments where `DATABASE_URL` does not start with `sqlite://`.

## Upstream / Downstream

**Instantiated by:** `db_factory.py` for MySQL URLs; also `database.py`'s `_ensure_pool` lazy-init path when auto-detecting MySQL.

**Implements:** `DatabaseBackend` (from `db_backend.py`).

**Depends on:** `aiomysql` for the connection pool and cursor operations.

## Design decisions

**`aiomysql.create_pool` for concurrency.** Unlike SQLite's single connection, MySQL supports many simultaneous connections. The pool size and recycle interval are configurable at construction time and default to 10 connections, 1-hour recycle. The pool is created at `initialize()`, not at construction, so the class can be instantiated synchronously.

**Transparent retry on InnoDB deadlocks (errno 1213).** `execute` and `execute_write` wrap the no-transaction path in `_retry_on_deadlock` — up to 3 attempts with 50/100/200 ms backoff + small jitter. The fix exists because cascading DELETE in `delete_agent` (77 rows × 13 tables) raced agent-run event INSERTs on EC2 2026-05-19 and surfaced as 4 `pymysql.err.OperationalError(1213, ...)` to ASGI clients. Retry is the canonical fix per MySQL docs. **Inside** an explicit transaction the wrapper is skipped on purpose: the caller owns the transaction boundary and re-running one statement would leave earlier ones un-rolled-back.

**`%s` placeholders, backtick-quoted identifiers.** MySQL uses `%s` for parameters and backticks for identifiers. All identifier strings passed to `get`, `insert`, etc. are validated by `_validate_identifier` (alphanumeric + underscore) and then backtick-quoted to avoid reserved-word collisions.

**`INSERT ... ON DUPLICATE KEY UPDATE ... AS new_row` for upserts.** The `upsert` method generates MySQL 8.0.20+ syntax using an alias (`new_row`) rather than the deprecated `VALUES()` function. This is more explicit and future-proof, but means the code will fail on MySQL versions older than 8.0.20.

**Transaction support via a task-scoped dedicated connection.** Transactions use a single connection acquired from the pool and stored in `self._txn_conn`, a **`ContextVar` holding `(owner_task, connection)`** — so the "am I inside a transaction?" answer is per asyncio task, not per backend instance. Every statement method goes through `_own_txn()` and falls back to `pool.acquire()` when the answer is `None`.

**为什么存 owner 而不是裸连接。** ContextVar 的语义是「**task 创建时拷贝一份父 context**」，不是「只有开启者可见」。事务开启**之后**创建的子 task 会继承那条连接，于是 `async with db.transaction():` 里写一个 `asyncio.gather(...)` 就会让子协程重新挤回同一条连接 —— 正是本文件要修的 two-coroutines-one-socket 条件，只是范围从「全进程」缩到「该请求的子树」；更糟的是子 task 调 `commit()` 会把父 task 还在用的连接归还，父 task 随后 commit 撞上 aiomysql 的 `assert conn in self._used`。

比较 task 身份把这层继承变成三种明确行为：

- **读**（`execute` / `get` 等）：子 task 走连接池拿自己的连接。`gather` 一堆查询是
  日常且无害的，代价只是读不到未提交的行。
- **写**（`execute_write` / `insert` / `update` / `delete` / `upsert`）：直接
  `RuntimeError`。它加入不了那个事务（那正是要修的 bug），而**悄悄换一条连接比报错更
  糟**——写会自动提交，外层 rollback 撤不掉它，调用方也永远不知道自己的写溜出了那个
  看起来正把它包住的事务。
- **commit / rollback**：子 task 直接 `RuntimeError`。结束别人的事务会归还父 task 还在
  写的连接，父 task 随后 commit 会撞上 aiomysql 的 `assert conn in self._used`。

子 task 仍可以 `begin_transaction()` 开自己的事务——继承来的值不算「已在事务中」。

**注意 sqlite 后端不是这个语义**（它单连接、且 `sqlite_proxy_server` 的事务本来就跨
请求跨 task，用的是服务端发的 `txn_id` token）。那边的同型缺陷单独处理，见
[[db_backend_sqlite.py]]。

The ContextVar is not a stylistic choice; it is the correctness boundary. `db_factory` hands out **one backend per event loop**, shared by every request, so instance-level transaction state was process-global state — see the 2026-08-17 section below.

`commit`/`rollback` clear the ContextVar in `finally` and close the connection before releasing it when the operation raised, so a connection with a desynced protocol stream is dropped rather than recycled.

**Value serialization mirrors `SQLiteBackend`.** `_serialize_value` converts `bool` to `0/1`, `datetime` to ISO 8601 strings, and `dict/list` to JSON strings. This ensures the two backends produce compatible stored representations so data written by MySQL can be read back under SQLite (and vice versa for the proxy path).

**IS NULL handling.** `get`, `update`, and `delete` filter clauses detect `None` values and generate `IS NULL` SQL instead of `= NULL`, which would always be false in MySQL.

## Gotchas

**MySQL 8.0.20+ upsert syntax.** The `INSERT ... AS new_row ON DUPLICATE KEY UPDATE new_row.col = ...` syntax requires MySQL 8.0.20 or later. Older MySQL versions reject this syntax with a parse error. If you need to support older MySQL, the `upsert` method needs modification to use the deprecated `VALUES(col)` form.

**Pool exhaustion under high concurrency.** The default pool size is 10. Long-running transactions or slow queries can hold connections, causing other coroutines to block waiting for a connection. Symptom: operations start timing out even though MySQL is healthy. Check `pool_size` against the expected concurrency.

**`_validate_identifier` rejects legitimate names with hyphens.** Column or table names containing hyphens (e.g., from external systems) will raise `ValueError` from `_validate_identifier`. This is intentional for SQL-injection prevention but can be surprising if you expect the validator to be lenient.

**New-contributor trap.** `aiomysql` cursors return tuples by default. `MySQLBackend` sets `cursorclass=aiomysql.DictCursor` to get dict rows. If you bypass the backend and use the raw pool directly, you will get tuples unless you explicitly pass the cursor class.

## 2026-08-17 — 事务连接从实例级改为 task 级（prod 事故根因）

**事故：** prod 09:37–09:56，登录与聊天历史全部 500，后端每次查询都是
`pymysql.err.InterfaceError: (0, 'Not connected')`，RDS 本身健康，只能靠重启恢复。

**根因链：**

1. 事务连接原先存在 `self._transaction_connection`（实例属性）。`db_factory`
   每个 event loop 只发一个 backend，于是这就是**进程级共享状态**。
2. 任一请求进入 `transaction()` 期间，**所有并发请求**的语句都命中
   `if self._transaction_connection is not None` 分支，被塞进同一条连接。触发者是
   `wipe_service` 的清空会话事务 —— 它在事务里串行跑几百次 delete，独占窗口极大。
3. 两个协程同时读同一 socket → aiomysql 抛
   `readexactly() called while another coroutine is already waiting for incoming data`
   → MySQL 协议流错位，连接死亡。
4. `commit()` 是**先 commit 再清空**且无 `try/finally`：commit 抛异常后
   `_transaction_connection` 永远指向那条死连接，此后进程内每条语句都改道到它。
   **永不自愈**，这是必须重启才能恢复的原因。

**修复：** ContextVar 隔离（爆炸半径回到单个 task）+ `commit`/`rollback` 用
`finally` 无条件清空 + 出错时先 `close()` 再 `release()`（aiomysql 的 `release()`
会丢弃已关闭连接，于是"毒化连接池"降级为"损失一条连接"）+ `begin()` 失败时立即
归还，避免连接泄漏。

**旧文档的教训：** 本文件此前那段写着"Concurrent calls ... are not safe; callers are
expected to use one backend instance per async task"——但 `db_factory` 的设计
恰恰保证了做不到。文档记下了危险，却把责任推给一个结构上不可能满足的前提，
等于没记。约束要写成代码里能成立的东西，写在文档里的承诺不算交付。

见 [[database.py]]（同一 bug 的第二份拷贝，一并修）与 [[db_factory.py]]（每 loop 单例）。

## 2026-08-07 — `get_by_ids` 支持 `fields`

沿用本文件 `get` 里已有的 `_validate_identifier` + backtick 写法；`id_field` 强制
并入投影，保证保序 map 可建。见 [[database.py]]。
