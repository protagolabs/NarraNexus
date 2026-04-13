"""
@file_name: test_schema_registry.py
@author: NarraNexus
@date: 2026-04-03
@description: Tests for the unified schema registry and auto-migration.

Covers DDL generation for both SQLite and MySQL, auto_migrate table creation,
column addition, index creation, and idempotency.
"""

from __future__ import annotations

import aiosqlite
import pytest
import tempfile
import os

from xyz_agent_context.utils.schema_registry import (
    TABLES,
    Column,
    Index,
    TableDef,
    auto_migrate,
    generate_create_table_sql,
    generate_mysql_ddl,
    generate_sqlite_ddl,
)


# ============================================================================
# Helpers
# ============================================================================

EXPECTED_TABLE_COUNT = 26


class _InMemorySQLiteBackend:
    """Minimal DatabaseBackend-like wrapper around aiosqlite for testing."""

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def dialect(self) -> str:
        return "sqlite"

    @property
    def placeholder(self) -> str:
        return "?"

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def execute(self, query: str, params=None) -> list[dict]:
        cursor = await self._conn.execute(query, params or ())
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def execute_write(self, query: str, params=None) -> int:
        cursor = await self._conn.execute(query, params or ())
        await self._conn.commit()
        return cursor.rowcount


# ============================================================================
# Tests: DDL Generation
# ============================================================================


class TestDDLGeneration:
    """Test DDL generation for all tables."""

    def test_all_tables_generate_sqlite_ddl(self):
        """Every registered table must produce valid SQLite DDL."""
        assert len(TABLES) == EXPECTED_TABLE_COUNT
        for name, table_def in TABLES.items():
            stmts = generate_sqlite_ddl(table_def)
            assert len(stmts) >= 1, f"Table {name} produced no DDL"
            assert stmts[0].startswith("CREATE TABLE IF NOT EXISTS"), (
                f"Table {name} DDL does not start with CREATE TABLE"
            )

    def test_all_tables_generate_mysql_ddl(self):
        """Every registered table must produce valid MySQL DDL."""
        for name, table_def in TABLES.items():
            stmts = generate_mysql_ddl(table_def)
            assert len(stmts) >= 1, f"Table {name} produced no DDL"
            assert stmts[0].startswith("CREATE TABLE IF NOT EXISTS"), (
                f"Table {name} DDL does not start with CREATE TABLE"
            )

    def test_generate_create_table_sql_dispatch(self):
        """generate_create_table_sql dispatches to the correct dialect."""
        table_def = TABLES["agents"]
        sqlite_stmts = generate_create_table_sql(table_def, "sqlite")
        mysql_stmts = generate_create_table_sql(table_def, "mysql")

        # SQLite should NOT have backticks
        assert "`" not in sqlite_stmts[0]
        # MySQL should have backticks
        assert "`" in mysql_stmts[0]

    def test_invalid_dialect_raises(self):
        """Unsupported dialect should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported dialect"):
            generate_create_table_sql(TABLES["agents"], "postgres")

    def test_composite_primary_key_sqlite(self):
        """Tables with composite PK should include PRIMARY KEY clause."""
        stmts = generate_sqlite_ddl(TABLES["bus_channel_members"])
        ddl = stmts[0]
        assert "PRIMARY KEY (channel_id, agent_id)" in ddl

    def test_composite_primary_key_mysql(self):
        """Tables with composite PK should include PRIMARY KEY clause in MySQL."""
        stmts = generate_mysql_ddl(TABLES["bus_channel_members"])
        ddl = stmts[0]
        assert "PRIMARY KEY (`channel_id`, `agent_id`)" in ddl

    def test_autoincrement_sqlite(self):
        """Auto-increment tables should have INTEGER PRIMARY KEY AUTOINCREMENT."""
        stmts = generate_sqlite_ddl(TABLES["agents"])
        ddl = stmts[0]
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in ddl

    def test_autoincrement_mysql(self):
        """Auto-increment tables should have AUTO_INCREMENT in MySQL."""
        stmts = generate_mysql_ddl(TABLES["agents"])
        ddl = stmts[0]
        assert "AUTO_INCREMENT" in ddl

    def test_index_count_matches(self):
        """Number of generated index statements should match table definition."""
        for name, table_def in TABLES.items():
            stmts = generate_sqlite_ddl(table_def)
            # First statement is CREATE TABLE, rest are indexes
            idx_stmts = [s for s in stmts if s.startswith("CREATE") and "INDEX" in s]
            assert len(idx_stmts) == len(table_def.indexes), (
                f"Table {name}: expected {len(table_def.indexes)} indexes, "
                f"got {len(idx_stmts)}"
            )


# ============================================================================
# Tests: Auto-Migration
# ============================================================================


class TestAutoMigrate:
    """Test auto_migrate on a real SQLite database."""

    @pytest.fixture
    async def backend(self, tmp_path):
        """Create an in-memory SQLite backend for testing."""
        db_path = str(tmp_path / "test.db")
        b = _InMemorySQLiteBackend(db_path)
        await b.initialize()
        yield b
        await b.close()

    async def test_creates_all_tables(self, backend):
        """auto_migrate should create all tables on an empty database."""
        await auto_migrate(backend)

        rows = await backend.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        created_tables = {row["name"] for row in rows}

        for table_name in TABLES:
            assert table_name in created_tables, (
                f"Table {table_name} was not created"
            )

    async def test_idempotent(self, backend):
        """Running auto_migrate twice should not raise errors."""
        await auto_migrate(backend)
        # Second run should be a no-op
        await auto_migrate(backend)

        rows = await backend.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        created_tables = {row["name"] for row in rows}
        for table_name in TABLES:
            assert table_name in created_tables

    async def test_adds_missing_column(self, backend):
        """auto_migrate should add a column that was added to the registry."""
        # First, create the table without the extra column
        await backend.execute_write(
            "CREATE TABLE IF NOT EXISTS _test_migrate_col ("
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "    name TEXT NOT NULL"
            ")"
        )

        # Register a table with an extra column
        test_table = TableDef(
            name="_test_migrate_col",
            columns=[
                Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False,
                       auto_increment=True, primary_key=True),
                Column("name", "TEXT", "VARCHAR(255)", nullable=False),
                Column("new_col", "TEXT", "VARCHAR(100)", default="'hello'"),
            ],
            indexes=[],
        )
        # Temporarily register
        TABLES["_test_migrate_col"] = test_table
        try:
            await auto_migrate(backend)

            # Verify the column was added
            rows = await backend.execute("PRAGMA table_info(_test_migrate_col)")
            col_names = {row["name"] for row in rows}
            assert "new_col" in col_names, "new_col was not added"
        finally:
            del TABLES["_test_migrate_col"]

    async def test_creates_missing_indexes(self, backend):
        """auto_migrate should create indexes that do not exist yet."""
        # Create table without indexes
        await backend.execute_write(
            "CREATE TABLE IF NOT EXISTS _test_migrate_idx ("
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "    category TEXT"
            ")"
        )

        test_table = TableDef(
            name="_test_migrate_idx",
            columns=[
                Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False,
                       auto_increment=True, primary_key=True),
                Column("category", "TEXT", "VARCHAR(64)"),
            ],
            indexes=[
                Index("idx_test_category", ["category"]),
            ],
        )
        TABLES["_test_migrate_idx"] = test_table
        try:
            await auto_migrate(backend)

            idx_rows = await backend.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='_test_migrate_idx'"
            )
            idx_names = {row["name"] for row in idx_rows}
            assert "idx_test_category" in idx_names, "Index was not created"
        finally:
            del TABLES["_test_migrate_idx"]

    async def test_not_null_column_without_default_gets_safe_default(self, backend):
        """Adding a NOT NULL column without default should get empty-string default in SQLite."""
        await backend.execute_write(
            "CREATE TABLE IF NOT EXISTS _test_notnull ("
            "    id INTEGER PRIMARY KEY AUTOINCREMENT"
            ")"
        )

        test_table = TableDef(
            name="_test_notnull",
            columns=[
                Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False,
                       auto_increment=True, primary_key=True),
                Column("required_col", "TEXT", "VARCHAR(64)", nullable=False),
            ],
            indexes=[],
        )
        TABLES["_test_notnull"] = test_table
        try:
            # Should not raise even though required_col has no default
            await auto_migrate(backend)

            rows = await backend.execute("PRAGMA table_info(_test_notnull)")
            col_names = {row["name"] for row in rows}
            assert "required_col" in col_names
        finally:
            del TABLES["_test_notnull"]


# ============================================================================
# Tests: Table Registry Completeness
# ============================================================================


class TestRegistryCompleteness:
    """Verify the registry contains all expected tables."""

    EXPECTED_TABLES = [
        "agents", "users", "events", "narratives",
        "mcp_urls", "inbox_table", "agent_messages", "module_instances",
        "instance_social_entities", "instance_jobs", "instance_rag_store",
        "instance_narrative_links", "instance_awareness",
        "instance_module_report_memory", "instance_json_format_memory",
        "instance_json_format_memory_chat", "module_report_memory",
        "cost_records",
        "embeddings_store",
        "bus_channels", "bus_channel_members", "bus_messages",
        "bus_agent_registry", "bus_message_failures",
    ]

    def test_all_expected_tables_registered(self):
        """All expected tables must be in the TABLES registry."""
        for name in self.EXPECTED_TABLES:
            assert name in TABLES, f"Table {name} is missing from registry"

    def test_no_unexpected_tables(self):
        """No unexpected tables should be in the registry."""
        for name in TABLES:
            if name.startswith("_test_"):
                continue
            assert name in self.EXPECTED_TABLES, (
                f"Unexpected table {name} in registry"
            )

    def test_table_count(self):
        """Registry should have exactly 24 tables."""
        assert len(TABLES) == EXPECTED_TABLE_COUNT

    def test_every_table_has_columns(self):
        """Every table must have at least one column."""
        for name, table_def in TABLES.items():
            assert len(table_def.columns) > 0, f"Table {name} has no columns"

    def test_every_column_has_both_types(self):
        """Every column must have both sqlite_type and mysql_type."""
        for name, table_def in TABLES.items():
            for col in table_def.columns:
                assert col.sqlite_type, (
                    f"Table {name}, column {col.name}: missing sqlite_type"
                )
                assert col.mysql_type, (
                    f"Table {name}, column {col.name}: missing mysql_type"
                )
