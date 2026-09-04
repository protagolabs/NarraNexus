"""
@file_name: test_register_table_owner.py
@author: Bin Liang
@date: 2026-09-03
@description: register_table converts a contracts TableSpec into a TableDef, enforces the ext_ prefix and owner, and plugin_settings is dual-dialect.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.table import ColumnSpec, IndexSpec, TableSpec
from xyz_agent_context.utils.db import schema_registry as sr


@pytest.fixture
def clean_registry():
    saved_tables, saved_owners = dict(sr.TABLES), dict(sr.TABLE_OWNERS)
    yield
    sr.TABLES.clear()
    sr.TABLES.update(saved_tables)
    sr.TABLE_OWNERS.clear()
    sr.TABLE_OWNERS.update(saved_owners)


def _spec(name="ext_acme_weather_items"):
    return TableSpec(
        name,
        (
            ColumnSpec("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
            ColumnSpec("city", "TEXT", "VARCHAR(64)", nullable=False),
        ),
        indexes=(IndexSpec("idx_acme_weather_city", ("city",), unique=True),),
    )


def test_register_table_converts_and_records_owner(clean_registry):
    table = sr.register_table(_spec(), owner="acme.weather")
    assert sr.TABLES["ext_acme_weather_items"] is table
    assert sr.TABLE_OWNERS["ext_acme_weather_items"] == "acme.weather"
    assert sr.tables_owned_by("acme.weather") == ["ext_acme_weather_items"]
    assert [c.mysql_type for c in table.columns] == ["BIGINT UNSIGNED", "VARCHAR(64)"]
    assert table.indexes[0].unique is True
    # both dialects generate DDL
    assert sr.generate_sqlite_ddl(table) and sr.generate_mysql_ddl(table)
    # idempotent for the same owner
    sr.register_table(_spec(), owner="acme.weather")


def test_register_table_refuses_wrong_prefix_and_foreign_owner(clean_registry):
    with pytest.raises(ValueError, match="prefixed"):
        sr.register_table(_spec("items"), owner="acme.weather")
    sr.register_table(_spec(), owner="acme.weather")
    with pytest.raises(ValueError, match="already registered"):
        sr.register_table(_spec(), owner="acme.other")
    with pytest.raises(ValueError, match="already registered"):
        sr.register_table(
            TableSpec("events", (ColumnSpec("id", "INTEGER", "BIGINT", primary_key=True),)), owner="builtin.chat"
        )


def test_core_tables_are_kernel_owned():
    assert sr.TABLE_OWNERS["events"] == sr.KERNEL_TABLE_OWNER


def test_plugin_settings_table_is_dual_dialect_with_unique_key():
    t = sr.TABLES["plugin_settings"]
    cols = {c.name: c for c in t.columns}
    assert cols["plugin_id"].mysql_type == "VARCHAR(128)" and cols["key"].mysql_type == "VARCHAR(64)"
    assert cols["is_secret"].default == "0"
    for c in t.columns:
        assert c.sqlite_type and c.mysql_type, c.name
    idx = {i.name: i for i in t.indexes}
    assert idx["idx_plugin_settings_plugin_key"].unique and idx["idx_plugin_settings_plugin_key"].columns == ["plugin_id", "key"]
    mysql = "\n".join(sr.generate_mysql_ddl(t))
    assert "CURRENT_TIMESTAMP(6)" in mysql and "MEDIUMTEXT" in mysql
