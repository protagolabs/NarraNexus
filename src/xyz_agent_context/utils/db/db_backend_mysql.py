"""
@file_name: db_backend_mysql.py
@author: NexusAgent
@date: 2026-04-02
@description: MySQL implementation of the DatabaseBackend interface

Uses aiomysql for async MySQL access. Designed for cloud/server deployment.

Key design decisions:
- Connection pool via aiomysql.create_pool (configurable size and recycle)
- %s parameter placeholders, backtick-quoted identifiers
- INSERT ... ON DUPLICATE KEY UPDATE with AS new_row syntax (MySQL 8.0.20+)
- Transaction support using a dedicated connection from the pool, bound to the
  calling asyncio task via a ContextVar (see `_txn_conn`) — NOT to the backend
  instance. A backend instance is shared by every request on the event loop, so
  an instance attribute made one caller's open transaction the implicit
  connection for every concurrent caller. See the class docstring.
- IS NULL handling for None filter values in get/update/delete
- JSON/dict/list values serialized to JSON strings for storage
- Boolean values stored as 0/1 integers
- datetime values stored as ISO 8601 strings
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiomysql
import pymysql.err
from loguru import logger

from xyz_agent_context.utils.db.db_backend import DatabaseBackend


# InnoDB deadlock errno. MySQL aborts the "lighter" transaction; the
# client is expected to retry. See:
# https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks-handling.html
_DEADLOCK_ERRNO = 1213


async def _retry_on_deadlock(
    coro_factory: Callable[[], Awaitable[Any]],
    max_attempts: int = 3,
) -> Any:
    """Re-invoke `coro_factory()` if it raises a MySQL deadlock error.

    Only retries on `pymysql.err.OperationalError` with errno 1213
    ("Deadlock found when trying to get lock; try restarting
    transaction"). Other OperationalError subtypes (e.g. 2003 / connect
    refused) and unrelated exceptions propagate immediately.

    Backoff is short and randomised — typical InnoDB deadlocks resolve
    in microseconds and we just need to give the surviving transaction
    time to commit.

    Callers must only wrap statement-level work that does NOT already
    sit inside an explicit transaction. Inside a transaction the boundary
    is owned by the caller; re-running a single statement would leave
    the earlier statements un-rolled-back.
    """
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except pymysql.err.OperationalError as e:
            if (
                e.args
                and e.args[0] == _DEADLOCK_ERRNO
                and attempt < max_attempts - 1
            ):
                # 50ms, 100ms, 200ms ... with up to 50ms jitter
                backoff = 0.05 * (2 ** attempt) + random.random() * 0.05
                logger.warning(
                    f"[MySQLBackend] deadlock (errno 1213) on attempt "
                    f"{attempt + 1}/{max_attempts}; retrying in "
                    f"{backoff:.3f}s"
                )
                await asyncio.sleep(backoff)
                continue
            raise
    # Unreachable — the loop either returns or raises.
    raise RuntimeError("unreachable")  # pragma: no cover


def _validate_identifier(identifier: str) -> str:
    """
    Validate table/column names to prevent SQL injection.

    Only allows alphanumeric characters and underscores.

    Args:
        identifier: The table or column name to validate.

    Raises:
        ValueError: If the identifier contains invalid characters.

    Returns:
        The validated identifier.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(
            f"Identifier '{identifier}' can only contain letters, digits, and underscores"
        )
    return identifier


def _serialize_value(value: Any) -> Any:
    """
    Serialize a Python value for MySQL storage.

    - dict/list -> JSON string
    - datetime -> ISO 8601 string
    - bool -> 0/1 integer
    - other types -> unchanged

    Args:
        value: The value to serialize.

    Returns:
        The serialized value suitable for MySQL.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class MySQLBackend(DatabaseBackend):
    """
    MySQL implementation of DatabaseBackend.

    Uses an aiomysql connection pool for high-concurrency async access.
    Transaction operations use a dedicated connection acquired from the pool.

    Transaction scope is the *asyncio task*, not the backend instance
    ------------------------------------------------------------------
    One backend instance serves every request on the event loop. Holding the
    open transaction's connection in an instance attribute therefore made it
    process-wide state: while any one caller sat inside `transaction()`, every
    OTHER concurrent caller's statement was silently routed onto that same
    connection instead of taking its own from the pool. Two coroutines then read
    the same socket, aiomysql raised "readexactly() called while another
    coroutine is already waiting for incoming data", the MySQL protocol stream
    desynced, and the connection died. Because the attribute was only cleared
    *after* a successful commit, the dead connection stayed installed and every
    subsequent statement in the process failed with
    `InterfaceError: (0, 'Not connected')` until the container was restarted.
    (Prod outage 2026-08-17 09:37–09:56; the trigger was a wide agent-data wipe,
    whose transaction holds the connection across hundreds of sequential
    deletes.)

    Binding the connection to a ContextVar fixes the blast radius: a value set
    inside a task is invisible to every other task, so a transaction can no
    longer capture unrelated callers' statements.

    Args:
        db_config: Dictionary with keys: host, port, user, password, database.
        pool_size: Maximum number of connections in the pool (default 10).
        pool_recycle: Connection recycle time in seconds (default 3600).
    """

    def __init__(
        self,
        db_config: Dict[str, Any],
        pool_size: int = 10,
        pool_recycle: int = 3600,
    ) -> None:
        self._db_config = db_config
        self._pool_size = pool_size
        self._pool_recycle = pool_recycle
        self._pool: Optional[aiomysql.Pool] = None
        # Per-instance ContextVar: the value is scoped to the calling task, so
        # concurrent requests never observe each other's transaction. Created in
        # __init__ rather than at module level so two backends (e.g. two
        # databases) cannot alias each other's transaction. Backends are
        # long-lived singletons — a handful per process — so this stays within
        # the "do not create ContextVars in hot paths" guidance.
        self._txn_conn: ContextVar[Optional[aiomysql.Connection]] = ContextVar(
            f"mysql_txn_conn_{id(self):x}", default=None
        )

    # ===== Properties =====

    @property
    def placeholder(self) -> str:
        return "%s"

    @property
    def dialect(self) -> str:
        return "mysql"

    # ===== Lifecycle =====

    async def initialize(self) -> None:
        """
        Create the aiomysql connection pool.

        Configures UTF-8 charset and autocommit mode.
        """
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

    async def close(self) -> None:
        """Close the connection pool and release all connections."""
        if self._pool is None:
            return

        # Only this task's transaction is reachable here — a ContextVar value
        # set in another task is by design invisible. That is fine: closing the
        # pool tears the remaining connections down anyway, and shutdown is the
        # one moment where abandoning an in-flight transaction is correct.
        conn = self._txn_conn.get()
        if conn is not None:
            self._return_to_pool(conn, broken=True)
            self._txn_conn.set(None)

        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None

    def _ensure_pool(self) -> aiomysql.Pool:
        """Return the pool, raising if not initialized."""
        if self._pool is None:
            raise RuntimeError("MySQLBackend is not initialized. Call initialize() first.")
        return self._pool

    # ===== Raw SQL Execution =====

    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a raw SQL query and return rows as dicts.

        Statement-level calls (no caller-owned transaction) are wrapped
        in `_retry_on_deadlock` so InnoDB errno 1213 is recovered
        transparently. Inside an explicit transaction the caller owns
        the boundary — we must NOT retry a single statement and leave
        earlier statements un-rolled-back.
        """
        pool = self._ensure_pool()

        txn = self._txn_conn.get()
        if txn is not None:
            async with txn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params or ())
                return await cursor.fetchall()

        async def _run():
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(query, params or ())
                    return await cursor.fetchall()

        return await _retry_on_deadlock(_run)

    async def execute_write(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> int:
        """Execute a write SQL statement, returning affected row count.

        See `execute` for the retry-on-deadlock contract.
        """
        pool = self._ensure_pool()

        txn = self._txn_conn.get()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, params or ())
                return cursor.rowcount

        async def _run():
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params or ())
                    return cursor.rowcount

        return await _retry_on_deadlock(_run)

    # ===== CRUD Operations =====

    async def get(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Query rows from a table with filtering, pagination, and sorting."""
        safe_table = _validate_identifier(table)

        if fields:
            safe_fields = [_validate_identifier(f) for f in fields]
            columns = ", ".join(f"`{f}`" for f in safe_fields)
        else:
            columns = "*"

        query = f"SELECT {columns} FROM `{safe_table}`"
        params: list[Any] = []

        if filters:
            where_clauses = []
            for key, value in filters.items():
                safe_key = _validate_identifier(key)
                if value is None:
                    where_clauses.append(f"`{safe_key}` IS NULL")
                else:
                    where_clauses.append(f"`{safe_key}` = %s")
                    params.append(_serialize_value(value))
            query += " WHERE " + " AND ".join(where_clauses)

        if order_by:
            order_parts = order_by.split()
            safe_order_field = _validate_identifier(order_parts[0])
            direction = ""
            if len(order_parts) > 1 and order_parts[1].upper() in ("ASC", "DESC"):
                direction = " " + order_parts[1].upper()
            query += f" ORDER BY `{safe_order_field}`{direction}"

        if limit is not None:
            query += f" LIMIT {int(limit)}"
        if offset is not None:
            query += f" OFFSET {int(offset)}"

        return await self.execute(query, tuple(params))

    async def get_one(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Query a single row matching the given filters."""
        results = await self.get(table, filters, limit=1)
        return results[0] if results else None

    async def get_by_ids(
        self,
        table: str,
        id_field: str,
        ids: List[str],
        fields: Optional[List[str]] = None,
    ) -> List[Optional[Dict[str, Any]]]:
        """Batch-fetch rows by IDs, preserving input order.

        ``fields`` narrows the projection, same contract as ``get``. Use it for
        existence checks on tables with fat columns — `SELECT *` there drags the
        payload across the wire only to discard it. It must include ``id_field``
        or the order-preserving map below cannot be built.
        """
        if not ids:
            return []

        unique_ids = list(dict.fromkeys(ids))
        safe_table = _validate_identifier(table)
        safe_id_field = _validate_identifier(id_field)

        if fields:
            safe_fields = [_validate_identifier(f) for f in dict.fromkeys([*fields, id_field])]
            columns = ", ".join(f"`{f}`" for f in safe_fields)
        else:
            columns = "*"

        placeholders = ",".join(["%s"] * len(unique_ids))
        query = f"SELECT {columns} FROM `{safe_table}` WHERE `{safe_id_field}` IN ({placeholders})"

        results = await self.execute(query, tuple(unique_ids))

        result_map = {row[id_field]: row for row in results}
        return [result_map.get(id_val) for id_val in ids]

    async def insert(
        self,
        table: str,
        data: Dict[str, Any],
    ) -> int:
        """Insert a single row, returning the lastrowid."""
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(key) for key in data.keys()]

        columns = ", ".join(f"`{key}`" for key in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))
        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders})"
        params = tuple(_serialize_value(v) for v in data.values())

        pool = self._ensure_pool()

        txn = self._txn_conn.get()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, params)
                return cursor.lastrowid or 0
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    return cursor.lastrowid or 0

    async def update(
        self,
        table: str,
        filters: Dict[str, Any],
        data: Dict[str, Any],
    ) -> int:
        """Update rows matching filters, returning the number of rows updated."""
        if not data:
            raise ValueError("Update data cannot be empty")
        if not filters:
            raise ValueError("Update operation must specify filter conditions")

        safe_table = _validate_identifier(table)

        set_clauses = []
        params: list[Any] = []
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

        query = (
            f"UPDATE `{safe_table}` "
            f"SET {', '.join(set_clauses)} "
            f"WHERE {' AND '.join(where_clauses)}"
        )

        pool = self._ensure_pool()

        txn = self._txn_conn.get()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, tuple(params))
                    return cursor.rowcount

    async def delete(
        self,
        table: str,
        filters: Dict[str, Any],
    ) -> int:
        """Delete rows matching filters, returning the number of rows deleted."""
        if not filters:
            raise ValueError("Delete operation must specify filter conditions")

        safe_table = _validate_identifier(table)

        where_clauses = []
        params: list[Any] = []
        for key, value in filters.items():
            safe_key = _validate_identifier(key)
            if value is None:
                where_clauses.append(f"`{safe_key}` IS NULL")
            else:
                where_clauses.append(f"`{safe_key}` = %s")
                params.append(_serialize_value(value))

        query = f"DELETE FROM `{safe_table}` WHERE {' AND '.join(where_clauses)}"

        pool = self._ensure_pool()

        txn = self._txn_conn.get()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, tuple(params))
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, tuple(params))
                    return cursor.rowcount

    async def upsert(
        self,
        table: str,
        data: Dict[str, Any],
        id_field: str,
    ) -> int:
        """
        Insert or update using INSERT ... ON DUPLICATE KEY UPDATE.

        Uses MySQL 8.0.20+ AS new_row syntax.

        Args:
            table: Table name.
            data: Column-value pairs to insert/update.
            id_field: The unique/primary key column for conflict detection.

        Returns:
            Number of affected rows (1=new insert, 2=updated existing).
        """
        if not data:
            raise ValueError("Insert data cannot be empty")

        safe_table = _validate_identifier(table)
        safe_keys = [_validate_identifier(key) for key in data.keys()]
        safe_id_field = _validate_identifier(id_field)

        columns = ", ".join(f"`{key}`" for key in safe_keys)
        placeholders = ", ".join(["%s"] * len(data))

        # Build ON DUPLICATE KEY UPDATE clause (excluding the id field)
        update_clauses = []
        for key in safe_keys:
            if key != safe_id_field:
                update_clauses.append(f"`{key}` = new_row.`{key}`")

        query = f"INSERT INTO `{safe_table}` ({columns}) VALUES ({placeholders}) AS new_row"
        if update_clauses:
            query += f" ON DUPLICATE KEY UPDATE {', '.join(update_clauses)}"

        params = tuple(_serialize_value(v) for v in data.values())

        pool = self._ensure_pool()

        txn = self._txn_conn.get()
        if txn is not None:
            async with txn.cursor() as cursor:
                await cursor.execute(query, params)
                return cursor.rowcount
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    return cursor.rowcount

    # ===== Transaction Support =====

    def _return_to_pool(self, conn: aiomysql.Connection, *, broken: bool) -> None:
        """Hand a transaction connection back to the pool.

        `broken=True` closes the socket first. A connection whose commit or
        rollback raised may have a desynced protocol stream: releasing it as-is
        would put it back on the free list, where the next unrelated caller
        would inherit the corruption. aiomysql's `release()` drops a closed
        connection instead of reusing it, so closing first is what turns
        "poison the pool" into "lose one connection".
        """
        if broken and not conn.closed:
            conn.close()
        pool = self._pool
        if pool is not None:
            pool.release(conn)

    async def begin_transaction(self) -> None:
        """Begin a transaction on a connection dedicated to the calling task."""
        if self._txn_conn.get() is not None:
            raise RuntimeError("Already in a transaction")

        pool = self._ensure_pool()
        conn = await pool.acquire()
        try:
            await conn.begin()
        except BaseException:
            # BEGIN failed — the caller gets the exception and will never call
            # commit/rollback, so release here or the connection leaks.
            self._return_to_pool(conn, broken=True)
            raise
        self._txn_conn.set(conn)

    async def commit(self) -> None:
        """Commit the current transaction and release the connection.

        The ContextVar is cleared in `finally`: if COMMIT raises, the connection
        is already unusable, and leaving it installed would route every later
        statement in this task onto a dead socket.
        """
        conn = self._txn_conn.get()
        if conn is None:
            raise RuntimeError("No active transaction")

        try:
            await conn.commit()
        except BaseException:
            self._return_to_pool(conn, broken=True)
            raise
        else:
            self._return_to_pool(conn, broken=False)
        finally:
            self._txn_conn.set(None)

    async def rollback(self) -> None:
        """Roll back the current transaction and release the connection.

        See `commit` for why the ContextVar is cleared unconditionally.
        """
        conn = self._txn_conn.get()
        if conn is None:
            raise RuntimeError("No active transaction")

        try:
            await conn.rollback()
        except BaseException:
            self._return_to_pool(conn, broken=True)
            raise
        else:
            self._return_to_pool(conn, broken=False)
        finally:
            self._txn_conn.set(None)
