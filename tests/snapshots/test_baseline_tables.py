"""
@file_name: test_baseline_tables.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the schema registry (every table, column, index) before it is split by domain.
"""
from __future__ import annotations

from tests.snapshots._approval import approve


def test_schema_registry_is_unchanged():
    from xyz_agent_context.utils.db.schema_registry import TABLES

    view = {}
    for name, table in TABLES.items():
        view[name] = {
            "columns": [
                [c.name, c.sqlite_type, c.mysql_type, bool(c.nullable), bool(c.primary_key)]
                for c in table.columns
            ],
            "indexes": sorted(
                [idx.name, list(idx.columns), bool(getattr(idx, "unique", False))]
                for idx in getattr(table, "indexes", []) or []
            ),
        }
    approve("tables", view)
