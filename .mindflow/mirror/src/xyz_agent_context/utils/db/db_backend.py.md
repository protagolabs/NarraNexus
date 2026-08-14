# db_backend.py

Abstract base class that every concrete database backend must implement — the contract that lets `AsyncDatabaseClient` stay database-agnostic.

## Why it exists

When SQLite support was added for the Tauri desktop migration, the team faced a choice: branch on the database type everywhere in `AsyncDatabaseClient`, or factor the driver-specific code into interchangeable backend objects. The ABC approach was chosen so that adding a new backend (e.g., PostgreSQL, or a remote proxy) requires implementing one class rather than modifying the shared client. `db_backend.py` defines that contract: the `DatabaseBackend` abstract class with `dialect`, `placeholder`, lifecycle methods, and a full set of CRUD operations.

## Upstream / Downstream

**Implemented by:** `db_backend_sqlite.py` (`SQLiteBackend`), `db_backend_mysql.py` (`MySQLBackend`), `db_backend_sqlite_proxy.py` (`SQLiteProxyBackend`).

**Used as a type by:** `database.py` (`AsyncDatabaseClient._backend: Optional[DatabaseBackend]`). All `execute`, `get`, `insert`, `update`, `delete`, `upsert`, and transaction calls on the client delegate to `self._backend`.

**Depends on:** nothing in the application — only Python stdlib `abc`.

## Design decisions

**`dialect` and `placeholder` as abstract properties.** These two properties drive the two remaining pieces of dialect awareness in `database.py`: `_mysql_to_sqlite_sql` is applied only when `backend.dialect == "sqlite"`, and raw `execute` calls that pass through the client still need the correct placeholder style.

**CRUD methods alongside raw `execute` / `execute_write`.** The interface offers both high-level helpers (`get`, `insert`, `update`, `delete`, `upsert`) and raw SQL execution. This matters because some callers need to issue complex JOINs or aggregations that can't be expressed with the dict-based helpers, yet still need the backend to handle connection management.

**`get_by_ids` in the interface.** The N+1 query problem was common enough that a batch-by-ID fetch is part of the contract rather than a convenience method. Every backend must implement it as a single `IN` query and return results in the same order as the input `ids` list.

**Transaction methods are abstract.** All backends must support `begin_transaction`, `commit`, and `rollback` even if the underlying driver makes transactions implicit. This keeps transaction handling uniform for callers in `agent_runtime/` that wrap multi-step operations.

## Gotchas

**Order contract on `get_by_ids`.** The interface requires results to be returned in the same order as the input `ids` list, with `None` in slots where an ID was not found. Backends that implement this with a simple `SELECT ... WHERE id IN (...)` must re-sort the results client-side. If an implementation skips this, callers that zip `ids` with results will silently misalign data.

**`execute` returns rows, `execute_write` returns affected count.** These are two separate abstract methods with different return types. A backend that makes `execute` return an affected count for writes will cause callers that expect `List[Dict]` to blow up unpredictably.

**New-contributor trap.** If you add a new method to `DatabaseBackend` without making it abstract (`@abstractmethod`), all three concrete backends silently inherit the default (which raises `NotImplementedError` at runtime). Make every new method `@abstractmethod` so the missing implementation is caught at class-definition time.

## 2026-08-07 — `get_by_ids` 的 `fields` 必须声明在 ABC 上

不是为了好看，是契约的**强制点**。`AsyncDatabaseClient` 把参数原样转发给
`db_factory` 选中的那个后端，所以一个只加在部分实现上的参数，等于对其余实现的
**每一次调用**都 TypeError——`fields=None` 也一样会作为关键字传过去，默认值救不了。

这个已经发生过一次：`fields` 加到了 MySQL 后端、SQLite 后端和 client 的转发上，唯独
漏了 proxy 后端。云端走 MySQL 一路绿，而 `run.sh` 和桌面 DMG（两者都设
`SQLITE_PROXY_URL`，因此走 proxy）的**每一次** `get_by_ids` 全炸——也就是所有
`BaseRepository` 批量取、收件箱、团队页、聊天上下文装载。铁律 #7 的字面场景。

下一个要给这里加参数的人：三个实现 + ABC + proxy 的 HTTP 两端，一个都不能少。
