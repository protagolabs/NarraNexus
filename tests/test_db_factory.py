"""
@file_name: test_db_factory.py
@author: NexusAgent
@date: 2026-04-02
@description: Tests for the db_factory URL detection and parsing utilities

Tests detect_backend_type() and parse_sqlite_url() without requiring
any database connections.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.utils.db_factory import detect_backend_type, parse_sqlite_url


# =============================================================================
# detect_backend_type
# =============================================================================


class TestDetectBackendType:
    """Tests for detect_backend_type()."""

    def test_sqlite_scheme(self) -> None:
        assert detect_backend_type("sqlite:///path/to/db.sqlite") == "sqlite"

    def test_sqlite_memory(self) -> None:
        assert detect_backend_type("sqlite:///:memory:") == "sqlite"

    def test_mysql_scheme(self) -> None:
        assert detect_backend_type("mysql://user:pass@localhost:3306/mydb") == "mysql"

    def test_mysql_connector_scheme(self) -> None:
        assert detect_backend_type("mysql+mysqlconnector://user:pass@host/db") == "mysql"

    def test_case_insensitive(self) -> None:
        assert detect_backend_type("SQLite:///path") == "sqlite"
        assert detect_backend_type("MySQL://user:pass@host/db") == "mysql"

    def test_unsupported_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported database URL scheme"):
            detect_backend_type("postgresql://user:pass@host/db")

    def test_no_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported database URL scheme"):
            detect_backend_type("/just/a/path")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported database URL scheme"):
            detect_backend_type("")


# =============================================================================
# parse_sqlite_url
# =============================================================================


class TestParseSqliteUrl:
    """Tests for parse_sqlite_url()."""

    def test_absolute_path(self) -> None:
        assert parse_sqlite_url("sqlite:///home/user/data.db") == "/home/user/data.db"

    def test_relative_path(self) -> None:
        assert parse_sqlite_url("sqlite://data.db") == "data.db"

    def test_memory_database(self) -> None:
        assert parse_sqlite_url("sqlite:///:memory:") == "/:memory:"

    def test_not_sqlite_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a sqlite URL"):
            parse_sqlite_url("mysql://user:pass@host/db")

    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="must include a path"):
            parse_sqlite_url("sqlite://")
