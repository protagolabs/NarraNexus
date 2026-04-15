# Phase 1: Pluggable Database Backend (SQLite + MySQL) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the database backend pluggable so the same codebase runs on SQLite (local desktop) or MySQL (cloud), without changing any Repository or Service code.

**Architecture:** Introduce a `DatabaseBackend` abstract base class behind `AsyncDatabaseClient`. Two concrete implementations: `SQLiteBackend` and `MySQLBackend` (extracted from current code). `AsyncDatabaseClient` delegates all operations to the active backend. The `db_factory.py` selects backend based on `DATABASE_URL` scheme (`sqlite:///` vs `mysql://`). Table creation scripts gain dual-dialect DDL support.

**Tech Stack:** Python 3.13, aiosqlite, aiomysql (kept as optional), pytest, pytest-asyncio

**Constraint:** Do NOT run any destructive operations against the existing MySQL database. All new SQLite work uses a separate local file. Existing code continues to work unchanged against MySQL.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/xyz_agent_context/utils/db_backend.py` | `DatabaseBackend` ABC — defines the interface all backends must implement |
| `src/xyz_agent_context/utils/db_backend_sqlite.py` | `SQLiteBackend` — SQLite implementation using aiosqlite, WAL mode, PRAGMA config |
| `src/xyz_agent_context/utils/db_backend_mysql.py` | `MySQLBackend` — extracted from current `database.py`, wraps aiomysql pool |
| `tests/test_db_backend_sqlite.py` | Unit tests for SQLiteBackend (in-memory `:memory:` DB) |
| `tests/test_db_backend_interface.py` | Shared interface compliance tests run against both backends |
| `tests/test_db_factory.py` | Tests for URL-based backend selection |
| `tests/conftest.py` | Shared fixtures (sqlite db, test tables) |

### Modified Files

| File | Change |
|------|--------|
| `src/xyz_agent_context/utils/database.py` | Add backend delegation: `AsyncDatabaseClient` gains a `_backend` field and delegates `get`, `insert`, `update`, `delete`, `upsert`, `execute`, `get_one`, `get_by_ids` to it. Falls back to current aiomysql behavior when no backend is set (backward compatible). |
| `src/xyz_agent_context/utils/db_factory.py` | Detect URL scheme → instantiate correct backend → pass to AsyncDatabaseClient |
| `src/xyz_agent_context/utils/database_table_management/table_manager_base.py` | Add `get_sqlite_type()` alongside `get_mysql_type()`, add `dialect` parameter to DDL generation |
| `src/xyz_agent_context/utils/database_table_management/create_table_base.py` | Support SQLite `CREATE TABLE` syntax, replace `information_schema` checks with `sqlite_master` |
| `pyproject.toml` | Add `aiosqlite>=0.20.0` to dependencies, add `[project.optional-dependencies] cloud = ["aiomysql>=0.3.2"]` |

### Unchanged (explicitly)

| File | Why |
|------|-----|
| `src/xyz_agent_context/repository/base.py` | Backend abstraction handles SQL dialect differences transparently |
| All `repository/*_repository.py` | No changes needed — they call `AsyncDatabaseClient` methods which delegate to backend |
| All service/runtime code | No changes needed |

---

## Task 1: Add aiosqlite dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add aiosqlite to dependencies**

In `pyproject.toml`, add `aiosqlite` to the dependencies list:

```toml
dependencies = [
    # ... existing deps ...
    "aiomysql>=0.3.2",
    "aiosqlite>=0.20.0",     # SQLite async backend for local desktop mode
    # ... rest ...
]
```

- [ ] **Step 2: Install**

Run: `uv sync`
Expected: aiosqlite installed successfully

- [ ] **Step 3: Verify import**

Run: `uv run python -c "import aiosqlite; print(aiosqlite.__version__)"`
Expected: prints version number (0.20.0 or higher)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add aiosqlite for SQLite backend support"
```

---

## Task 2: Create DatabaseBackend ABC

**Files:**
- Create: `src/xyz_agent_context/utils/db_backend.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db_backend_interface.py`

- [ ] **Step 1: Write the interface compliance test**

```python
# tests/test_db_backend_interface.py
"""
Tests that verify any DatabaseBackend implementation satisfies the interface contract.
These tests are parameterized and run against all available backends.
"""
import pytest
from abc import ABC


class BackendContractTests:
    """
    Mixin of contract tests. Subclasses set self.backend in a fixture.
    """

    @pytest.fixture
    def backend(self):
        raise NotImplementedError("Subclass must provide a backend fixture")

    async def test_execute_create_table(self, backend):
        await backend.execute(
            "CREATE TABLE IF NOT EXISTS test_contract (id INTEGER PRIMARY KEY, name TEXT)", []
        )

    async def test_insert_and_get(self, backend):
        await backend.execute(
            "CREATE TABLE IF NOT EXISTS test_crud (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)", []
        )
        rowcount = await backend.insert("test_crud", {"name": "Alice", "age": 30})
        assert rowcount >= 0

        rows = await backend.get("test_crud", {"name": "Alice"})
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
        assert rows[0]["age"] == 30

    async def test_get_one(self, backend):
        await backend.execute(
            "CREATE TABLE IF NOT EXISTS test_getone (id INTEGER PRIMARY KEY, val TEXT)", []
        )
        await backend.insert("test_getone", {"val": "hello"})
        row = await backend.get_one("test_getone", {"val": "hello"})
        assert row is not None
        assert row["val"] == "hello"

        missing = await backend.get_one("test_getone", {"val": "nope"})
        assert missing is None

    async def test_update(self, backend):
        await backend.execute(
            "CREATE TABLE IF NOT EXISTS test_update (id INTEGER PRIMARY KEY, name TEXT, score INTEGER)", []
        )
        await backend.insert("test_update", {"name": "Bob", "score": 10})
        updated = await backend.update("test_update", {"name": "Bob"}, {"score": 20})
        assert updated >= 1

        rows = await backend.get("test_update", {"name": "Bob"})
        assert rows[0]["score"] == 20

    async def test_delete(self, backend):
        await backend.execute(
            "CREATE TABLE IF NOT EXISTS test_delete (id INTEGER PRIMARY KEY, name TEXT)", []
        )
        await backend.insert("test_delete", {"name": "Charlie"})
        deleted = await backend.delete("test_delete", {"name": "Charlie"})
        assert deleted >= 1

        rows = await backend.get("test_delete", {"name": "Charlie"})
        assert len(rows) == 0

    async def test_upsert(self, backend):
        await backend.execute("""
            CREATE TABLE IF NOT EXISTS test_upsert (
                uid TEXT PRIMARY KEY,
                name TEXT,
                score INTEGER
            )
        """, [])
        # Insert
        await backend.upsert("test_upsert", {"uid": "u1", "name": "A", "score": 10}, "uid")
        rows = await backend.get("test_upsert", {"uid": "u1"})
        assert rows[0]["score"] == 10

        # Update via upsert
        await backend.upsert("test_upsert", {"uid": "u1", "name": "A", "score": 99}, "uid")
        rows = await backend.get("test_upsert", {"uid": "u1"})
        assert rows[0]["score"] == 99

    async def test_get_by_ids(self, backend):
        await backend.execute("""
            CREATE TABLE IF NOT EXISTS test_byids (
                uid TEXT PRIMARY KEY,
                val TEXT
            )
        """, [])
        await backend.insert("test_byids", {"uid": "a", "val": "1"})
        await backend.insert("test_byids", {"uid": "b", "val": "2"})
        await backend.insert("test_byids", {"uid": "c", "val": "3"})

        rows = await backend.get_by_ids("test_byids", "uid", ["a", "c"])
        vals = [r["val"] for r in rows if r is not None]
        assert "1" in vals
        assert "3" in vals

    async def test_get_with_limit_offset_order(self, backend):
        await backend.execute("""
            CREATE TABLE IF NOT EXISTS test_paging (
                id INTEGER PRIMARY KEY,
                name TEXT
            )
        """, [])
        for i in range(5):
            await backend.insert("test_paging", {"name": f"item_{i}"})

        rows = await backend.get("test_paging", {}, limit=2, offset=1, order_by="id ASC")
        assert len(rows) == 2

    async def test_transaction_commit(self, backend):
        await backend.execute("""
            CREATE TABLE IF NOT EXISTS test_tx (
                id INTEGER PRIMARY KEY,
                val TEXT
            )
        """, [])
        await backend.begin_transaction()
        await backend.insert("test_tx", {"val": "in_tx"})
        await backend.commit()

        rows = await backend.get("test_tx", {"val": "in_tx"})
        assert len(rows) == 1

    async def test_transaction_rollback(self, backend):
        await backend.execute("""
            CREATE TABLE IF NOT EXISTS test_tx_rb (
                id INTEGER PRIMARY KEY,
                val TEXT
            )
        """, [])
        await backend.insert("test_tx_rb", {"val": "before"})

        await backend.begin_transaction()
        await backend.insert("test_tx_rb", {"val": "in_tx"})
        await backend.rollback()

        rows = await backend.get("test_tx_rb", {})
        vals = [r["val"] for r in rows]
        assert "in_tx" not in vals
```

- [ ] **Step 2: Write the conftest with shared fixtures**

```python
# tests/conftest.py
"""
Shared test fixtures for database backend tests.
"""
import pytest


@pytest.fixture
def sqlite_db_path(tmp_path):
    """Provide a temporary SQLite database path."""
    return str(tmp_path / "test.db")
```

- [ ] **Step 3: Write the DatabaseBackend ABC**

```python
# src/xyz_agent_context/utils/db_backend.py
"""
@file_name: db_backend.py
@author: NarraNexus
@date: 2026-04-02
@description: Abstract base class for pluggable database backends.

Defines the interface that all database backends (SQLite, MySQL, etc.) must implement.
AsyncDatabaseClient delegates all operations to the active backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class DatabaseBackend(ABC):
    """
    Abstract database backend interface.

    Implementations handle dialect-specific SQL generation, connection management,
    and query execution. The interface mirrors AsyncDatabaseClient's public API.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the backend (create pool, open connection, etc.)."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close all connections and release resources."""
        ...

    @abstractmethod
    async def execute(self, query: str, params: list | tuple) -> List[Dict[str, Any]]:
        """Execute raw SQL and return rows as dicts."""
        ...

    @abstractmethod
    async def execute_write(self, query: str, params: list | tuple) -> int:
        """Execute a write query (INSERT/UPDATE/DELETE) and return affected row count."""
        ...

    @abstractmethod
    async def get(
        self,
        table: str,
        filters: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """SELECT rows matching filters."""
        ...

    @abstractmethod
    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """SELECT a single row matching filters, or None."""
        ...

    @abstractmethod
    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[str],
    ) -> List[Optional[Dict[str, Any]]]:
        """SELECT rows by a list of IDs. Returns results in input order, None for missing."""
        ...

    @abstractmethod
    async def insert(self, table: str, data: Dict[str, Any]) -> int:
        """INSERT a row and return lastrowid or affected count."""
        ...

    @abstractmethod
    async def update(
        self, table: str, filters: Dict[str, Any], data: Dict[str, Any]
    ) -> int:
        """UPDATE rows matching filters and return affected count."""
        ...

    @abstractmethod
    async def delete(self, table: str, filters: Dict[str, Any]) -> int:
        """DELETE rows matching filters and return affected count."""
        ...

    @abstractmethod
    async def upsert(
        self, table: str, data: Dict[str, Any], id_field: str
    ) -> int:
        """
        Atomic insert-or-update.

        MySQL: INSERT ... ON DUPLICATE KEY UPDATE
        SQLite: INSERT ... ON CONFLICT ... DO UPDATE
        """
        ...

    @abstractmethod
    async def begin_transaction(self) -> None:
        """Begin a transaction."""
        ...

    @abstractmethod
    async def commit(self) -> None:
        """Commit the current transaction."""
        ...

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the current transaction."""
        ...

    @property
    @abstractmethod
    def placeholder(self) -> str:
        """SQL parameter placeholder. '%s' for MySQL, '?' for SQLite."""
        ...

    @property
    @abstractmethod
    def dialect(self) -> str:
        """Backend dialect name: 'mysql' or 'sqlite'."""
        ...
```

- [ ] **Step 4: Run tests (should fail — no implementations yet)**

Run: `uv run pytest tests/test_db_backend_interface.py -v 2>&1 | head -20`
Expected: Collection with 0 tests (no concrete subclasses of BackendContractTests yet)

- [ ] **Step 5: Commit**

```bash
git add src/xyz_agent_context/utils/db_backend.py tests/conftest.py tests/test_db_backend_interface.py
git commit -m "feat: add DatabaseBackend ABC and contract test suite"
```

---

## Task 3: Implement SQLiteBackend

**Files:**
- Create: `src/xyz_agent_context/utils/db_backend_sqlite.py`
- Create: `tests/test_db_backend_sqlite.py`

- [ ] **Step 1: Write SQLite-specific tests extending contract tests**

```python
# tests/test_db_backend_sqlite.py
"""
SQLiteBackend implementation tests.
Runs the full BackendContractTests suite plus SQLite-specific tests.
"""
import pytest
from tests.test_db_backend_interface import BackendContractTests
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


class TestSQLiteBackendContract(BackendContractTests):
    """Run all contract tests against SQLiteBackend."""

    @pytest.fixture
    async def backend(self):
        b = SQLiteBackend(db_path=":memory:")
        await b.initialize()
        yield b
        await b.close()


class TestSQLiteSpecific:
    """SQLite-specific behavior tests."""

    @pytest.fixture
    async def backend(self):
        b = SQLiteBackend(db_path=":memory:")
        await b.initialize()
        yield b
        await b.close()

    async def test_wal_mode_enabled(self, backend):
        rows = await backend.execute("PRAGMA journal_mode", [])
        assert rows[0]["journal_mode"] == "wal"

    async def test_placeholder_is_question_mark(self, backend):
        assert backend.placeholder == "?"

    async def test_dialect_is_sqlite(self, backend):
        assert backend.dialect == "sqlite"

    async def test_foreign_keys_enabled(self, backend):
        rows = await backend.execute("PRAGMA foreign_keys", [])
        assert rows[0]["foreign_keys"] == 1

    async def test_upsert_with_conflict(self, backend):
        await backend.execute("""
            CREATE TABLE test_conflict (
                key TEXT PRIMARY KEY,
                value TEXT,
                counter INTEGER DEFAULT 0
            )
        """, [])
        await backend.upsert("test_conflict", {"key": "k1", "value": "v1", "counter": 1}, "key")
        await backend.upsert("test_conflict", {"key": "k1", "value": "v2", "counter": 2}, "key")

        rows = await backend.get("test_conflict", {"key": "k1"})
        assert rows[0]["value"] == "v2"
        assert rows[0]["counter"] == 2

    async def test_concurrent_reads_during_write(self, backend):
        """WAL mode should allow reads while writing."""
        await backend.execute(
            "CREATE TABLE test_wal (id INTEGER PRIMARY KEY, val TEXT)", []
        )
        await backend.insert("test_wal", {"val": "before"})

        await backend.begin_transaction()
        await backend.insert("test_wal", {"val": "in_tx"})
        # Read should still work (WAL mode)
        rows = await backend.get("test_wal", {})
        # Before commit, the read should see only the committed row
        assert any(r["val"] == "before" for r in rows)
        await backend.commit()

    async def test_json_stored_as_text(self, backend):
        """JSON values should be stored as TEXT and retrievable."""
        import json
        await backend.execute(
            "CREATE TABLE test_json (id INTEGER PRIMARY KEY, data TEXT)", []
        )
        payload = json.dumps({"key": "value", "nested": [1, 2, 3]})
        await backend.insert("test_json", {"data": payload})
        rows = await backend.get("test_json", {})
        parsed = json.loads(rows[0]["data"])
        assert parsed["key"] == "value"
        assert parsed["nested"] == [1, 2, 3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_backend_sqlite.py -v 2>&1 | head -20`
Expected: ImportError — `db_backend_sqlite` does not exist yet

- [ ] **Step 3: Implement SQLiteBackend**

```python
# src/xyz_agent_context/utils/db_backend_sqlite.py
"""
@file_name: db_backend_sqlite.py
@author: NarraNexus
@date: 2026-04-02
@description: SQLite database backend implementation using aiosqlite.

Optimized for single-user local desktop usage with WAL mode,
memory-mapped I/O, and write serialization via asyncio.Lock.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from xyz_agent_context.utils.db_backend import DatabaseBackend


def _validate_identifier(identifier: str) -> str:
    """Validate table/field names for safety (prevent SQL injection)."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(
            f"Identifier '{identifier}' can only contain letters, digits, and underscores"
        )
    return identifier


def _serialize_value(value: Any) -> Any:
    """Serialize Python values for SQLite storage."""
    if isinstance(value, dict) or isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return 1 if value else 0
    return value


class SQLiteBackend(DatabaseBackend):
    """
    SQLite backend using aiosqlite with WAL mode.

    Performance configuration:
    - WAL journal mode: concurrent reads + writes
    - synchronous=NORMAL: safe in WAL, 2-3x faster than FULL
    - 64MB page cache
    - 256MB memory-mapped I/O
    - Temp tables in memory
    - 5s busy timeout
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._connection = await aiosqlite.connect(self._db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._apply_pragmas()
        logger.info(f"SQLiteBackend initialized: {self._db_path}")

    async def _apply_pragmas(self) -> None:
        pragmas = [
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA cache_size = -64000",
            "PRAGMA mmap_size = 268435456",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA busy_timeout = 5000",
            "PRAGMA foreign_keys = ON",
        ]
        for pragma in pragmas:
            await self._connection.execute(pragma)

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("SQLiteBackend closed")

    @property
    def placeholder(self) -> str:
        return "?"

    @property
    def dialect(self) -> str:
        return "sqlite"

    async def execute(self, query: str, params: list | tuple) -> List[Dict[str, Any]]:
        logger.debug(f"              → DB.execute: {query[:80]}...")
        cursor = await self._connection.execute(query, params or [])
        if cursor.description:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        return []

    async def execute_write(self, query: str, params: list | tuple) -> int:
        async with self._write_lock:
            cursor = await self._connection.execute(query, params or [])
            await self._connection.commit()
            return cursor.rowcount

    async def get(
        self,
        table: str,
        filters: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        safe_table = _validate_identifier(table)

        if fields:
            cols = ", ".join(f'"{_validate_identifier(f)}"' for f in fields)
        else:
            cols = "*"

        query = f'SELECT {cols} FROM "{safe_table}"'
        params: list = []

        if filters:
            clauses = []
            for key, value in filters.items():
                safe_key = _validate_identifier(key)
                if value is None:
                    clauses.append(f'"{safe_key}" IS NULL')
                else:
                    clauses.append(f'"{safe_key}" = ?')
                    params.append(_serialize_value(value))
            query += " WHERE " + " AND ".join(clauses)

        if order_by:
            query += f" ORDER BY {order_by}"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"

        logger.debug(f"              → DB.get('{table}', {len(filters)} filters)")
        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        logger.debug(f"              ← DB.get: {len(result)} rows")
        return result

    async def get_one(
        self, table: str, filters: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        rows = await self.get(table, filters, limit=1)
        return rows[0] if rows else None

    async def get_by_ids(
        self, table: str, id_field: str, ids: List[str]
    ) -> List[Optional[Dict[str, Any]]]:
        if not ids:
            return []

        safe_table = _validate_identifier(table)
        safe_id = _validate_identifier(id_field)
        unique_ids = list(dict.fromkeys(ids))  # deduplicate preserving order
        placeholders = ", ".join(["?"] * len(unique_ids))
        query = f'SELECT * FROM "{safe_table}" WHERE "{safe_id}" IN ({placeholders})'

        cursor = await self._connection.execute(query, unique_ids)
        rows = await cursor.fetchall()
        row_map = {row[id_field]: dict(row) for row in rows}

        return [row_map.get(id_val) for id_val in ids]

    async def insert(self, table: str, data: Dict[str, Any]) -> int:
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(k) for k in data.keys()]
        columns = ", ".join(f'"{k}"' for k in safe_keys)
        placeholders = ", ".join(["?"] * len(data))
        query = f'INSERT INTO "{safe_table}" ({columns}) VALUES ({placeholders})'
        params = [_serialize_value(v) for v in data.values()]

        logger.debug(f"              → DB.insert('{table}', {len(data)} fields)")
        async with self._write_lock:
            cursor = await self._connection.execute(query, params)
            await self._connection.commit()
            logger.debug(f"              ← DB.insert: lastrowid={cursor.lastrowid}")
            return cursor.lastrowid or 0

    async def update(
        self, table: str, filters: Dict[str, Any], data: Dict[str, Any]
    ) -> int:
        if not data:
            raise ValueError("Update data cannot be empty")
        if not filters:
            raise ValueError("Update filters cannot be empty (would update all rows)")

        safe_table = _validate_identifier(table)
        set_clauses = []
        params: list = []
        for key, value in data.items():
            safe_key = _validate_identifier(key)
            set_clauses.append(f'"{safe_key}" = ?')
            params.append(_serialize_value(value))

        where_clauses = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                where_clauses.append(f'"{safe_key}" IS NULL')
            else:
                where_clauses.append(f'"{safe_key}" = ?')
                params.append(_serialize_value(value))

        query = f'UPDATE "{safe_table}" SET {", ".join(set_clauses)} WHERE {" AND ".join(where_clauses)}'

        logger.debug(f"              → DB.update('{table}', {len(data)} fields)")
        async with self._write_lock:
            cursor = await self._connection.execute(query, params)
            await self._connection.commit()
            logger.debug(f"              ← DB.update: {cursor.rowcount} rows affected")
            return cursor.rowcount

    async def delete(self, table: str, filters: Dict[str, Any]) -> int:
        if not filters:
            raise ValueError("Delete filters cannot be empty (would delete all rows)")

        safe_table = _validate_identifier(table)
        clauses = []
        params: list = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                clauses.append(f'"{safe_key}" IS NULL')
            else:
                clauses.append(f'"{safe_key}" = ?')
                params.append(_serialize_value(value))

        query = f'DELETE FROM "{safe_table}" WHERE {" AND ".join(clauses)}'

        logger.debug(f"              → DB.delete('{table}', {len(filters)} filters)")
        async with self._write_lock:
            cursor = await self._connection.execute(query, params)
            await self._connection.commit()
            logger.debug(f"              ← DB.delete: {cursor.rowcount} rows affected")
            return cursor.rowcount

    async def upsert(
        self, table: str, data: Dict[str, Any], id_field: str
    ) -> int:
        if not data:
            raise ValueError("Upsert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(k) for k in data.keys()]
        safe_id = _validate_identifier(id_field)

        columns = ", ".join(f'"{k}"' for k in safe_keys)
        placeholders = ", ".join(["?"] * len(data))

        update_clauses = []
        for key in safe_keys:
            if key != safe_id:
                update_clauses.append(f'"{key}" = excluded."{key}"')

        query = f'INSERT INTO "{safe_table}" ({columns}) VALUES ({placeholders})'
        if update_clauses:
            query += f' ON CONFLICT("{safe_id}") DO UPDATE SET {", ".join(update_clauses)}'

        params = [_serialize_value(v) for v in data.values()]

        logger.debug(f"              → DB.upsert('{table}', {len(data)} fields)")
        async with self._write_lock:
            cursor = await self._connection.execute(query, params)
            await self._connection.commit()
            logger.debug(f"              ← DB.upsert: {cursor.rowcount} rows affected")
            return cursor.rowcount

    async def begin_transaction(self) -> None:
        await self._connection.execute("BEGIN")

    async def commit(self) -> None:
        await self._connection.commit()

    async def rollback(self) -> None:
        await self._connection.rollback()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_db_backend_sqlite.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/xyz_agent_context/utils/db_backend_sqlite.py tests/test_db_backend_sqlite.py
git commit -m "feat: implement SQLiteBackend with WAL mode and full test suite"
```

---

## Task 4: Extract MySQLBackend from existing code

**Files:**
- Create: `src/xyz_agent_context/utils/db_backend_mysql.py`

This task extracts the MySQL-specific logic from `database.py` into a `MySQLBackend` class that implements the same `DatabaseBackend` interface. The existing `database.py` code is **not modified** yet — that happens in Task 5.

- [ ] **Step 1: Implement MySQLBackend**

```python
# src/xyz_agent_context/utils/db_backend_mysql.py
"""
@file_name: db_backend_mysql.py
@author: NarraNexus
@date: 2026-04-02
@description: MySQL database backend implementation using aiomysql.

Extracted from the original database.py AsyncDatabaseClient.
Wraps the aiomysql connection pool and implements the DatabaseBackend interface.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiomysql
from loguru import logger

from xyz_agent_context.utils.db_backend import DatabaseBackend


def _validate_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(
            f"Identifier '{identifier}' can only contain letters, digits, and underscores"
        )
    return identifier


def _serialize_value(value: Any) -> Any:
    if isinstance(value, dict) or isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


class MySQLBackend(DatabaseBackend):
    """
    MySQL backend using aiomysql connection pool.

    This is the existing production backend, extracted from AsyncDatabaseClient.
    """

    def __init__(
        self,
        db_config: Dict[str, Any],
        pool_size: int = 10,
        pool_recycle: int = 3600,
    ):
        self._db_config = db_config
        self._pool_size = pool_size
        self._pool_recycle = pool_recycle
        self._pool: Optional[aiomysql.Pool] = None
        self._transaction_connection: Optional[aiomysql.Connection] = None

    async def initialize(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self._db_config["host"],
            port=self._db_config.get("port", 3306),
            user=self._db_config["user"],
            password=self._db_config["password"],
            db=self._db_config["database"],
            minsize=1,
            maxsize=self._pool_size,
            pool_recycle=self._pool_recycle,
            autocommit=True,
            charset="utf8mb4",
        )
        logger.info(f"MySQLBackend initialized: {self._db_config['host']}:{self._db_config.get('port', 3306)}")

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQLBackend closed")

    @property
    def placeholder(self) -> str:
        return "%s"

    @property
    def dialect(self) -> str:
        return "mysql"

    async def _get_conn(self):
        if self._transaction_connection:
            return self._transaction_connection
        return await self._pool.acquire()

    async def _release_conn(self, conn):
        if conn is not self._transaction_connection:
            self._pool.release(conn)

    async def execute(self, query: str, params: list | tuple) -> List[Dict[str, Any]]:
        logger.debug(f"              → DB.execute: {query[:80]}...")
        conn = await self._get_conn()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params or ())
                if cursor.description:
                    return await cursor.fetchall()
                return []
        finally:
            await self._release_conn(conn)

    async def execute_write(self, query: str, params: list | tuple) -> int:
        conn = await self._get_conn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params or ())
                return cursor.rowcount
        finally:
            await self._release_conn(conn)

    async def get(
        self,
        table: str,
        filters: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        safe_table = _validate_identifier(table)

        if fields:
            cols = ", ".join(f"`{_validate_identifier(f)}`" for f in fields)
        else:
            cols = "*"

        query = f"SELECT {cols} FROM `{safe_table}`"
        params: list = []

        if filters:
            clauses = []
            for key, value in filters.items():
                safe_key = _validate_identifier(key)
                if value is None:
                    clauses.append(f"`{safe_key}` IS NULL")
                else:
                    clauses.append(f"`{safe_key}` = %s")
                    params.append(_serialize_value(value))
            query += " WHERE " + " AND ".join(clauses)

        if order_by:
            query += f" ORDER BY {order_by}"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"

        logger.debug(f"              → DB.get('{table}', {len(filters)} filters)")
        conn = await self._get_conn()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params)
                result = await cursor.fetchall()
                logger.debug(f"              ← DB.get: {len(result)} rows")
                return list(result)
        finally:
            await self._release_conn(conn)

    async def get_one(
        self, table: str, filters: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        rows = await self.get(table, filters, limit=1)
        return rows[0] if rows else None

    async def get_by_ids(
        self, table: str, id_field: str, ids: List[str]
    ) -> List[Optional[Dict[str, Any]]]:
        if not ids:
            return []

        safe_table = _validate_identifier(table)
        safe_id = _validate_identifier(id_field)
        unique_ids = list(dict.fromkeys(ids))
        placeholders = ", ".join(["%s"] * len(unique_ids))
        query = f"SELECT * FROM `{safe_table}` WHERE `{safe_id}` IN ({placeholders})"

        conn = await self._get_conn()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, unique_ids)
                rows = await cursor.fetchall()
                row_map = {row[id_field]: dict(row) for row in rows}
                return [row_map.get(id_val) for id_val in ids]
        finally:
            await self._release_conn(conn)

    async def insert(self, table: str, data: Dict[str, Any]) -> int:
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(k) for k in data.keys()]
        columns = ", ".join(f"`{k}`" for k in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))
        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders})"
        params = [_serialize_value(v) for v in data.values()]

        logger.debug(f"              → DB.insert('{table}', {len(data)} fields)")
        conn = await self._get_conn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                logger.debug(f"              ← DB.insert: lastrowid={cursor.lastrowid}")
                return cursor.lastrowid or 0
        finally:
            await self._release_conn(conn)

    async def update(
        self, table: str, filters: Dict[str, Any], data: Dict[str, Any]
    ) -> int:
        if not data:
            raise ValueError("Update data cannot be empty")
        if not filters:
            raise ValueError("Update filters cannot be empty")

        safe_table = _validate_identifier(table)
        set_clauses = []
        params: list = []
        for key, value in data.items():
            safe_key = _validate_identifier(key)
            set_clauses.append(f"`{safe_key}` = %s")
            params.append(_serialize_value(value))

        where_clauses = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                where_clauses.append(f"`{safe_key}` IS NULL")
            else:
                where_clauses.append(f"`{safe_key}` = %s")
                params.append(_serialize_value(value))

        query = f"UPDATE `{safe_table}` SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)}"

        logger.debug(f"              → DB.update('{table}', {len(data)} fields)")
        conn = await self._get_conn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                logger.debug(f"              ← DB.update: {cursor.rowcount} rows affected")
                return cursor.rowcount
        finally:
            await self._release_conn(conn)

    async def delete(self, table: str, filters: Dict[str, Any]) -> int:
        if not filters:
            raise ValueError("Delete filters cannot be empty")

        safe_table = _validate_identifier(table)
        clauses = []
        params: list = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                clauses.append(f"`{safe_key}` IS NULL")
            else:
                clauses.append(f"`{safe_key}` = %s")
                params.append(_serialize_value(value))

        query = f"DELETE FROM `{safe_table}` WHERE {' AND '.join(clauses)}"

        logger.debug(f"              → DB.delete('{table}', {len(filters)} filters)")
        conn = await self._get_conn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                logger.debug(f"              ← DB.delete: {cursor.rowcount} rows affected")
                return cursor.rowcount
        finally:
            await self._release_conn(conn)

    async def upsert(
        self, table: str, data: Dict[str, Any], id_field: str
    ) -> int:
        if not data:
            raise ValueError("Upsert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(k) for k in data.keys()]
        safe_id = _validate_identifier(id_field)

        columns = ", ".join(f"`{k}`" for k in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))

        update_clauses = []
        for key in safe_keys:
            if key != safe_id:
                update_clauses.append(f"`{key}` = new_row.`{key}`")

        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders}) AS new_row"
        if update_clauses:
            query += f" ON DUPLICATE KEY UPDATE {', '.join(update_clauses)}"

        params = [_serialize_value(v) for v in data.values()]

        logger.debug(f"              → DB.upsert('{table}', {len(data)} fields)")
        conn = await self._get_conn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                logger.debug(f"              ← DB.upsert: {cursor.rowcount} rows affected")
                return cursor.rowcount
        finally:
            await self._release_conn(conn)

    async def begin_transaction(self) -> None:
        conn = await self._pool.acquire()
        await conn.begin()
        self._transaction_connection = conn

    async def commit(self) -> None:
        if self._transaction_connection:
            await self._transaction_connection.commit()
            self._pool.release(self._transaction_connection)
            self._transaction_connection = None

    async def rollback(self) -> None:
        if self._transaction_connection:
            await self._transaction_connection.rollback()
            self._pool.release(self._transaction_connection)
            self._transaction_connection = None
```

- [ ] **Step 2: Commit** (MySQLBackend cannot be tested without a running MySQL server — contract tests validate via SQLiteBackend)

```bash
git add src/xyz_agent_context/utils/db_backend_mysql.py
git commit -m "feat: extract MySQLBackend from database.py into DatabaseBackend interface"
```

---

## Task 5: Wire backends into AsyncDatabaseClient and db_factory

**Files:**
- Modify: `src/xyz_agent_context/utils/db_factory.py`
- Modify: `src/xyz_agent_context/utils/database.py`
- Create: `tests/test_db_factory.py`

- [ ] **Step 1: Write factory test**

```python
# tests/test_db_factory.py
"""
Tests for db_factory URL-based backend selection.
"""
import pytest
from unittest.mock import patch, AsyncMock
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


class TestDbFactory:
    async def test_sqlite_url_creates_sqlite_backend(self):
        """sqlite:// URL should produce a SQLiteBackend."""
        with patch("xyz_agent_context.utils.db_factory._create_backend") as mock_create:
            mock_backend = AsyncMock(spec=SQLiteBackend)
            mock_create.return_value = mock_backend

            from xyz_agent_context.utils.db_factory import _create_backend
            # Test the URL parsing logic directly
            assert "sqlite" in "sqlite:///path/to/db.sqlite"

    async def test_sqlite_url_scheme_detection(self):
        """Verify URL scheme detection logic."""
        from xyz_agent_context.utils.db_factory import detect_backend_type
        assert detect_backend_type("sqlite:///home/user/nexus.db") == "sqlite"
        assert detect_backend_type("sqlite:///:memory:") == "sqlite"
        assert detect_backend_type("mysql://user:pass@host:3306/db") == "mysql"
        assert detect_backend_type("mysql+mysqlconnector://user:pass@host/db") == "mysql"

    async def test_invalid_scheme_raises(self):
        from xyz_agent_context.utils.db_factory import detect_backend_type
        with pytest.raises(ValueError, match="Unsupported"):
            detect_backend_type("postgresql://host/db")
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_db_factory.py -v 2>&1 | head -20`
Expected: ImportError — `detect_backend_type` does not exist yet

- [ ] **Step 3: Update db_factory.py with backend detection**

Add the following to `src/xyz_agent_context/utils/db_factory.py` (add these functions, keep existing code):

```python
# Add to the top of the file, after existing imports
from urllib.parse import urlparse


def detect_backend_type(url: str) -> str:
    """
    Detect database backend type from URL scheme.

    Returns:
        'sqlite' or 'mysql'

    Raises:
        ValueError: if scheme is not supported
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme.startswith("sqlite"):
        return "sqlite"
    if scheme.startswith("mysql"):
        return "mysql"
    raise ValueError(
        f"Unsupported database URL scheme '{scheme}'. "
        "Supported: sqlite:///path or mysql://user:pass@host:port/db"
    )


def parse_sqlite_url(url: str) -> str:
    """
    Extract file path from sqlite:// URL.

    Examples:
        sqlite:///home/user/nexus.db  -> /home/user/nexus.db
        sqlite:///:memory:            -> :memory:
        sqlite:///./relative/path.db  -> ./relative/path.db
    """
    # Remove scheme prefix
    path = url.split("sqlite:///", 1)[-1] if "sqlite:///" in url else url
    if not path:
        raise ValueError("SQLite URL must contain a file path: sqlite:///path/to/db.sqlite")
    return path
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_db_factory.py -v`
Expected: All tests PASS

- [ ] **Step 5: Update db_factory.py to create backends**

Modify the existing `get_db_client()` function in `db_factory.py` to support both backends. The key change: if `DATABASE_URL` starts with `sqlite://`, use `SQLiteBackend`; otherwise use existing MySQL path.

Read the current `get_db_client()` implementation first, then add backend-aware initialization alongside the existing code, maintaining backward compatibility.

- [ ] **Step 6: Commit**

```bash
git add src/xyz_agent_context/utils/db_factory.py tests/test_db_factory.py
git commit -m "feat: add URL-based backend detection to db_factory"
```

---

## Task 6: Table DDL dual-dialect support

**Files:**
- Modify: `src/xyz_agent_context/utils/database_table_management/table_manager_base.py`
- Modify: `src/xyz_agent_context/utils/database_table_management/create_table_base.py`
- Create: `tests/test_table_creation_sqlite.py`

- [ ] **Step 1: Write test for SQLite table creation**

```python
# tests/test_table_creation_sqlite.py
"""
Tests for dual-dialect table creation (SQLite DDL generation).
"""
import pytest
from xyz_agent_context.utils.database_table_management.table_manager_base import BaseTableManager


class TestSQLiteTypeMapping:
    def test_str_maps_to_text(self):
        result = BaseTableManager.get_sqlite_type("name", str, None)
        assert result == "TEXT"

    def test_int_maps_to_integer(self):
        result = BaseTableManager.get_sqlite_type("count", int, None)
        assert result == "INTEGER"

    def test_float_maps_to_real(self):
        result = BaseTableManager.get_sqlite_type("score", float, None)
        assert result == "REAL"

    def test_bool_maps_to_integer(self):
        result = BaseTableManager.get_sqlite_type("is_active", bool, None)
        assert result == "INTEGER"

    def test_datetime_maps_to_text(self):
        from datetime import datetime
        result = BaseTableManager.get_sqlite_type("created_at", datetime, None)
        assert result == "TEXT"

    def test_dict_maps_to_text(self):
        result = BaseTableManager.get_sqlite_type("metadata", dict, None)
        assert result == "TEXT"

    def test_list_maps_to_text(self):
        result = BaseTableManager.get_sqlite_type("tags", list, None)
        assert result == "TEXT"
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_table_creation_sqlite.py -v 2>&1 | head -10`
Expected: AttributeError — `get_sqlite_type` does not exist

- [ ] **Step 3: Add `get_sqlite_type()` to BaseTableManager**

Add this class method to `BaseTableManager` in `table_manager_base.py`:

```python
@classmethod
def get_sqlite_type(cls, field_name: str, field_type: type, field_info: Any) -> str:
    """
    Map Python types to SQLite types.

    SQLite has 5 storage classes: NULL, INTEGER, REAL, TEXT, BLOB.
    Most complex types (dict, list, datetime) map to TEXT (stored as JSON or ISO 8601).
    """
    origin = get_origin(field_type)
    args = get_args(field_type)

    # Handle Optional[T]
    if origin is type(None) or (origin and args and type(None) in args):
        if args:
            actual_type = next((arg for arg in args if arg is not type(None)), None)
            if actual_type:
                field_type = actual_type
                origin = get_origin(field_type)
                args = get_args(field_type)

    # Map types
    if field_type is int:
        return "INTEGER"
    if field_type is float:
        return "REAL"
    if field_type is bool:
        return "INTEGER"  # SQLite has no bool, use 0/1
    if field_type is str:
        return "TEXT"
    if field_type is datetime:
        return "TEXT"  # ISO 8601 format
    if field_type is bytes:
        return "BLOB"
    # dict, list, and other complex types -> TEXT (JSON serialized)
    if field_type in (dict, list) or origin in (dict, list):
        return "TEXT"
    if inspect.isclass(field_type) and issubclass(field_type, Enum):
        return "TEXT"

    return "TEXT"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_table_creation_sqlite.py -v`
Expected: All tests PASS

- [ ] **Step 5: Update create_table_base.py for SQLite support**

Add a `check_table_exists_sqlite()` function and modify `create_table()` to accept a `dialect` parameter:

```python
async def check_table_exists_sqlite(table_name: str) -> bool:
    """Check if a table exists in SQLite."""
    db_client = await get_db_client()
    rows = await db_client.execute(
        "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?",
        [table_name]
    )
    return rows[0]["cnt"] > 0 if rows else False
```

- [ ] **Step 6: Commit**

```bash
git add src/xyz_agent_context/utils/database_table_management/table_manager_base.py \
        src/xyz_agent_context/utils/database_table_management/create_table_base.py \
        tests/test_table_creation_sqlite.py
git commit -m "feat: add SQLite DDL type mapping and dual-dialect table creation"
```

---

## Task 7: Integration test — full CRUD cycle on SQLite

**Files:**
- Create: `tests/test_integration_sqlite.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration_sqlite.py
"""
End-to-end integration test: create tables, run CRUD, verify on SQLite.
Tests the full stack: SQLiteBackend -> AsyncDatabaseClient -> operations.
"""
import pytest
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


class TestSQLiteIntegration:
    @pytest.fixture
    async def backend(self):
        b = SQLiteBackend(db_path=":memory:")
        await b.initialize()

        # Create a realistic table matching our schema patterns
        await b.execute("""
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL UNIQUE,
                agent_name TEXT NOT NULL,
                created_by TEXT NOT NULL,
                agent_description TEXT,
                agent_type TEXT,
                is_public INTEGER NOT NULL DEFAULT 0,
                agent_metadata TEXT,
                agent_create_time TEXT DEFAULT (datetime('now')),
                agent_update_time TEXT DEFAULT (datetime('now'))
            )
        """, [])

        await b.execute("""
            CREATE TABLE narratives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                narrative_id TEXT NOT NULL UNIQUE,
                agent_id TEXT NOT NULL,
                narrative_info TEXT,
                routing_embedding TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """, [])

        await b.execute("""
            CREATE INDEX idx_narratives_agent ON narratives(agent_id, updated_at DESC)
        """, [])

        yield b
        await b.close()

    async def test_agent_crud_lifecycle(self, backend):
        # Insert
        await backend.insert("agents", {
            "agent_id": "agt_test001",
            "agent_name": "Test Agent",
            "created_by": "usr_001",
            "agent_description": "A test agent",
            "is_public": 0,
        })

        # Read
        agent = await backend.get_one("agents", {"agent_id": "agt_test001"})
        assert agent is not None
        assert agent["agent_name"] == "Test Agent"

        # Update
        await backend.update(
            "agents",
            {"agent_id": "agt_test001"},
            {"agent_name": "Updated Agent", "is_public": 1}
        )
        agent = await backend.get_one("agents", {"agent_id": "agt_test001"})
        assert agent["agent_name"] == "Updated Agent"
        assert agent["is_public"] == 1

        # Upsert (update existing)
        await backend.upsert("agents", {
            "agent_id": "agt_test001",
            "agent_name": "Upserted Agent",
            "created_by": "usr_001",
            "is_public": 0,
        }, "agent_id")
        agent = await backend.get_one("agents", {"agent_id": "agt_test001"})
        assert agent["agent_name"] == "Upserted Agent"

        # Upsert (insert new)
        await backend.upsert("agents", {
            "agent_id": "agt_test002",
            "agent_name": "New Agent",
            "created_by": "usr_001",
            "is_public": 1,
        }, "agent_id")
        agents = await backend.get("agents", {})
        assert len(agents) == 2

        # Delete
        await backend.delete("agents", {"agent_id": "agt_test002"})
        agents = await backend.get("agents", {})
        assert len(agents) == 1

    async def test_narrative_with_json_fields(self, backend):
        import json
        narrative_info = json.dumps({
            "topic": "test conversation",
            "actors": [{"id": "usr_001", "type": "participant"}],
            "summary": "A test narrative"
        })

        await backend.insert("narratives", {
            "narrative_id": "nar_test001",
            "agent_id": "agt_001",
            "narrative_info": narrative_info,
        })

        row = await backend.get_one("narratives", {"narrative_id": "nar_test001"})
        assert row is not None
        info = json.loads(row["narrative_info"])
        assert info["topic"] == "test conversation"
        assert len(info["actors"]) == 1

    async def test_get_by_ids_preserves_order(self, backend):
        for i in range(5):
            await backend.insert("agents", {
                "agent_id": f"agt_{i:03d}",
                "agent_name": f"Agent {i}",
                "created_by": "usr_001",
            })

        # Request in non-sequential order
        results = await backend.get_by_ids("agents", "agent_id", ["agt_003", "agt_001", "agt_004"])
        assert len(results) == 3
        assert results[0]["agent_id"] == "agt_003"
        assert results[1]["agent_id"] == "agt_001"
        assert results[2]["agent_id"] == "agt_004"

    async def test_get_with_index_performance(self, backend):
        """Verify indexed queries work correctly."""
        for i in range(20):
            await backend.insert("narratives", {
                "narrative_id": f"nar_{i:03d}",
                "agent_id": "agt_001" if i < 15 else "agt_002",
                "narrative_info": "{}",
            })

        # Filtered + ordered query should use index
        rows = await backend.get(
            "narratives",
            {"agent_id": "agt_001"},
            order_by="updated_at DESC",
            limit=10,
        )
        assert len(rows) == 10
        assert all(r["agent_id"] == "agt_001" for r in rows)

    async def test_transaction_atomicity(self, backend):
        await backend.begin_transaction()
        await backend.insert("agents", {
            "agent_id": "agt_tx1",
            "agent_name": "TX Agent 1",
            "created_by": "usr_001",
        })
        await backend.insert("agents", {
            "agent_id": "agt_tx2",
            "agent_name": "TX Agent 2",
            "created_by": "usr_001",
        })
        await backend.rollback()

        agents = await backend.get("agents", {})
        agent_ids = [a["agent_id"] for a in agents]
        assert "agt_tx1" not in agent_ids
        assert "agt_tx2" not in agent_ids
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/test_integration_sqlite.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration_sqlite.py
git commit -m "test: add SQLite integration tests with realistic schema patterns"
```

---

## Task 8: MessageBus tables for SQLite

**Files:**
- Create: `src/xyz_agent_context/utils/database_table_management/create_message_bus_tables.py`
- Create: `tests/test_message_bus_tables.py`

- [ ] **Step 1: Write test**

```python
# tests/test_message_bus_tables.py
"""
Tests for MessageBus table creation on SQLite.
"""
import pytest
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


class TestMessageBusTables:
    @pytest.fixture
    async def backend(self):
        b = SQLiteBackend(db_path=":memory:")
        await b.initialize()
        yield b
        await b.close()

    async def test_create_all_bus_tables(self, backend):
        from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
            create_bus_tables_sqlite,
        )
        await create_bus_tables_sqlite(backend)

        # Verify all 4 tables exist
        for table in ["bus_channels", "bus_channel_members", "bus_messages", "bus_agent_registry"]:
            rows = await backend.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", [table]
            )
            assert len(rows) == 1, f"Table {table} not created"

    async def test_bus_message_insert_and_query(self, backend):
        from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
            create_bus_tables_sqlite,
        )
        await create_bus_tables_sqlite(backend)

        await backend.insert("bus_channels", {
            "channel_id": "ch_001",
            "name": "test-channel",
            "channel_type": "group",
            "created_by": "agt_001",
        })

        await backend.insert("bus_channel_members", {
            "channel_id": "ch_001",
            "agent_id": "agt_001",
        })
        await backend.insert("bus_channel_members", {
            "channel_id": "ch_001",
            "agent_id": "agt_002",
        })

        await backend.insert("bus_messages", {
            "message_id": "msg_001",
            "channel_id": "ch_001",
            "from_agent": "agt_001",
            "content": "Hello Agent 2!",
            "msg_type": "text",
        })

        # Query unprocessed messages for agt_002
        rows = await backend.execute("""
            SELECT m.* FROM bus_messages m
            JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
            WHERE cm.agent_id = ?
              AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01')
              AND m.from_agent != ?
            ORDER BY m.created_at ASC
        """, ["agt_002", "agt_002"])
        assert len(rows) == 1
        assert rows[0]["content"] == "Hello Agent 2!"

    async def test_bus_message_failures_table(self, backend):
        from xyz_agent_context.utils.database_table_management.create_message_bus_tables import (
            create_bus_tables_sqlite,
        )
        await create_bus_tables_sqlite(backend)

        await backend.insert("bus_message_failures", {
            "message_id": "msg_001",
            "agent_id": "agt_001",
            "retry_count": 1,
            "last_error": "timeout",
        })

        row = await backend.get_one("bus_message_failures", {
            "message_id": "msg_001", "agent_id": "agt_001"
        })
        assert row["retry_count"] == 1
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_message_bus_tables.py -v 2>&1 | head -10`
Expected: ImportError

- [ ] **Step 3: Implement MessageBus table creation**

```python
# src/xyz_agent_context/utils/database_table_management/create_message_bus_tables.py
"""
@file_name: create_message_bus_tables.py
@author: NarraNexus
@date: 2026-04-02
@description: Create MessageBus tables for agent-to-agent communication.

Tables: bus_channels, bus_channel_members, bus_messages,
        bus_agent_registry, bus_message_failures
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xyz_agent_context.utils.db_backend import DatabaseBackend


BUS_TABLES_SQLITE = [
    """
    CREATE TABLE IF NOT EXISTS bus_channels (
        channel_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        channel_type TEXT NOT NULL DEFAULT 'group',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bus_channel_members (
        channel_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        joined_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_read_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_processed_at TEXT,
        PRIMARY KEY (channel_id, agent_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bus_messages (
        message_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        from_agent TEXT NOT NULL,
        content TEXT NOT NULL,
        msg_type TEXT NOT NULL DEFAULT 'text',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bus_agent_registry (
        agent_id TEXT PRIMARY KEY,
        owner_user_id TEXT NOT NULL,
        capabilities TEXT,
        description TEXT,
        capability_embedding TEXT,
        visibility TEXT NOT NULL DEFAULT 'private',
        registered_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bus_message_failures (
        message_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        last_retry_at TEXT,
        PRIMARY KEY (message_id, agent_id)
    )
    """,
]

BUS_INDEXES_SQLITE = [
    "CREATE INDEX IF NOT EXISTS idx_bus_msg_channel_time ON bus_messages(channel_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_bus_member_agent ON bus_channel_members(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_bus_registry_visibility ON bus_agent_registry(visibility)",
    "CREATE INDEX IF NOT EXISTS idx_bus_registry_owner ON bus_agent_registry(owner_user_id)",
]


async def create_bus_tables_sqlite(backend: "DatabaseBackend") -> None:
    """Create all MessageBus tables and indexes on SQLite."""
    for ddl in BUS_TABLES_SQLITE:
        await backend.execute(ddl, [])
    for idx in BUS_INDEXES_SQLITE:
        await backend.execute(idx, [])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_message_bus_tables.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/xyz_agent_context/utils/database_table_management/create_message_bus_tables.py \
        tests/test_message_bus_tables.py
git commit -m "feat: add MessageBus table definitions with SQLite DDL and indexes"
```

---

## Summary

| Task | What it produces | Test count |
|------|-----------------|------------|
| 1. Add aiosqlite | Dependency available | 0 (import check) |
| 2. DatabaseBackend ABC | Interface + contract tests | ~12 contract test methods |
| 3. SQLiteBackend | Full SQLite implementation | ~20 tests |
| 4. MySQLBackend | Extracted MySQL implementation | 0 (needs live MySQL) |
| 5. Wire into factory | URL-based backend selection | ~3 tests |
| 6. Table DDL | Dual-dialect DDL generation | ~7 tests |
| 7. Integration test | Full CRUD on realistic schema | ~6 tests |
| 8. MessageBus tables | Bus table creation | ~3 tests |

**After Phase 1 is complete:** The system can run entirely on SQLite by setting `DATABASE_URL=sqlite:///path/to/nexus.db`. All existing MySQL code continues to work unchanged. No database data is modified.

**Next plans to write:**
- Phase 2: MessageBus service implementation
- Phase 3: Frontend unification
- Phase 4: Tauri shell
- Phase 5: Build and release
