"""
@file_name: test_db_integration_wiring.py
@author: NexusAgent
@date: 2026-04-02
@description: Integration tests verifying AsyncDatabaseClient delegates to DatabaseBackend

Tests that AsyncDatabaseClient correctly delegates all CRUD and transaction
operations to a SQLiteBackend when created via create_with_backend().
Also verifies that the default (no-backend) path remains unchanged.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.database import AsyncDatabaseClient


@pytest.fixture
async def sqlite_db():
    """Create an AsyncDatabaseClient backed by an in-memory SQLite database."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()

    db = await AsyncDatabaseClient.create_with_backend(backend)

    # Create a test table
    await backend.execute(
        "CREATE TABLE test_wiring ("
        "  id TEXT PRIMARY KEY,"
        "  name TEXT,"
        "  score INTEGER"
        ")",
        [],
    )

    yield db

    await db.close()


class TestBackendDelegation:
    """Verify AsyncDatabaseClient delegates operations to the backend."""

    @pytest.mark.asyncio
    async def test_insert_and_get(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        rows = await sqlite_db.get("test_wiring", {"id": "1"})
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
        assert rows[0]["score"] == 100

    @pytest.mark.asyncio
    async def test_get_one(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row is not None
        assert row["name"] == "Alice"

        missing = await sqlite_db.get_one("test_wiring", {"id": "nonexistent"})
        assert missing is None

    @pytest.mark.asyncio
    async def test_get_with_no_filters(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})
        await sqlite_db.insert("test_wiring", {"id": "2", "name": "Bob", "score": 200})

        rows = await sqlite_db.get("test_wiring", {})
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_update(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        affected = await sqlite_db.update("test_wiring", {"id": "1"}, {"name": "Bob"})
        assert affected == 1

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_delete(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})
        await sqlite_db.insert("test_wiring", {"id": "2", "name": "Bob", "score": 200})

        affected = await sqlite_db.delete("test_wiring", {"id": "2"})
        assert affected == 1

        rows = await sqlite_db.get("test_wiring", {})
        assert len(rows) == 1
        assert rows[0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_upsert_insert(self, sqlite_db: AsyncDatabaseClient):
        affected = await sqlite_db.upsert(
            "test_wiring", {"id": "1", "name": "Alice", "score": 100}, "id"
        )
        assert affected >= 1

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_upsert_update(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        await sqlite_db.upsert(
            "test_wiring", {"id": "1", "name": "Alice Updated", "score": 200}, "id"
        )

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row["name"] == "Alice Updated"
        assert row["score"] == 200

    @pytest.mark.asyncio
    async def test_get_by_ids(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})
        await sqlite_db.insert("test_wiring", {"id": "2", "name": "Bob", "score": 200})
        await sqlite_db.insert("test_wiring", {"id": "3", "name": "Charlie", "score": 300})

        results = await sqlite_db.get_by_ids("test_wiring", "id", ["3", "1"])
        assert len(results) == 2
        assert results[0]["name"] == "Charlie"
        assert results[1]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_get_with_limit_and_order(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})
        await sqlite_db.insert("test_wiring", {"id": "2", "name": "Bob", "score": 300})
        await sqlite_db.insert("test_wiring", {"id": "3", "name": "Charlie", "score": 200})

        rows = await sqlite_db.get("test_wiring", {}, limit=2, order_by="score DESC")
        assert len(rows) == 2
        assert rows[0]["score"] == 300
        assert rows[1]["score"] == 200

    @pytest.mark.asyncio
    async def test_execute_raw_query(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        rows = await sqlite_db.execute(
            "SELECT name FROM test_wiring WHERE id = ?", ("1",), fetch=True
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_execute_write(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        result = await sqlite_db.execute(
            "UPDATE test_wiring SET score = ? WHERE id = ?", (999, "1"), fetch=False
        )
        # execute with fetch=False delegates to execute_write, returns rowcount
        assert result == 1

    @pytest.mark.asyncio
    async def test_insert_filters_none_values(self, sqlite_db: AsyncDatabaseClient):
        """None values should be filtered out before delegation."""
        await sqlite_db.insert("test_wiring", {"id": "1", "name": None, "score": 100})

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row is not None
        assert row["score"] == 100
        # name was filtered out, should be NULL
        assert row["name"] is None

    @pytest.mark.asyncio
    async def test_transaction_commit(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.begin_transaction()
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})
        await sqlite_db.commit()

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row is not None
        assert row["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_transaction_rollback(self, sqlite_db: AsyncDatabaseClient):
        await sqlite_db.insert("test_wiring", {"id": "1", "name": "Alice", "score": 100})

        await sqlite_db.begin_transaction()
        await sqlite_db.update("test_wiring", {"id": "1"}, {"name": "SHOULD_ROLLBACK"})
        await sqlite_db.rollback()

        row = await sqlite_db.get_one("test_wiring", {"id": "1"})
        assert row["name"] == "Alice"


class TestNoBackendFallback:
    """Verify that without a backend, the old MySQL path is used."""

    @pytest.mark.asyncio
    async def test_no_backend_field_is_none(self):
        db = AsyncDatabaseClient()
        assert db._backend is None
        assert db._pool is None

    @pytest.mark.asyncio
    async def test_create_with_backend_sets_backend(self):
        backend = SQLiteBackend(":memory:")
        await backend.initialize()

        db = await AsyncDatabaseClient.create_with_backend(backend)
        assert db._backend is backend
        assert db._pool is None
        assert db._initialized is True

        await db.close()
