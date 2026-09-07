"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-09-07
@description: Repo-root pytest configuration: registration happens only at boot, so the test process loads the builtins into the process registries ONCE at collection (backend role, NOT frozen so a test can still register a fake) — for tests/ and plugins/*/tests alike. Private ``Registries()`` call ``load_builtins`` themselves.
"""
from __future__ import annotations


def _load_builtins_for_tests() -> None:
    from narranexus.kernel.plugins.builtins import load_builtins
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    if not KERNEL_REGISTRIES.frozen and not KERNEL_REGISTRIES.registry_for("turn.pipeline.act.framework").names():
        load_builtins(KERNEL_REGISTRIES, "backend")


_load_builtins_for_tests()
