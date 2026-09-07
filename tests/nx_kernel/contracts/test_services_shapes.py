"""
@file_name: test_services_shapes.py
@author: Bin Liang
@date: 2026-09-07
@description: The kernel.* service contracts match the implementations they were traced from (no silent drift).
"""
from __future__ import annotations

import inspect

from narranexus.contracts import services
from narranexus.platform.utils.db import db_backend
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend


def _sig(cls, name):
    return inspect.signature(getattr(cls, name))


def test_database_backend_contract_matches_the_abc_it_was_traced_from():
    for method in ("execute", "execute_write", "probe"):
        contract, real = _sig(services.DatabaseBackend, method), _sig(db_backend.DatabaseBackend, method)
        assert list(contract.parameters) == list(real.parameters), method
        for p in contract.parameters:
            assert contract.parameters[p].default == real.parameters[p].default, (method, p)
    for prop in ("dialect", "placeholder"):
        assert isinstance(getattr(db_backend.DatabaseBackend, prop), property), prop
        assert isinstance(getattr(services.DatabaseBackend, prop), property), prop


def test_a_real_backend_satisfies_the_contract_structurally():
    assert isinstance(SQLiteBackend(":memory:"), services.DatabaseBackend)


def test_the_slot_tree_points_at_this_contract():
    from narranexus.kernel.plugins.slots import build_kernel_slot_tree

    assert build_kernel_slot_tree().get("kernel.db").contract == "narranexus.contracts.services:DatabaseBackend"
