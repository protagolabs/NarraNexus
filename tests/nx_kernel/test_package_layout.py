"""
@file_name: test_package_layout.py
@author: Bin Liang
@date: 2026-09-03
@description: The narranexus package exists and its layering holds at import time.

import-linter enforces the same rule statically in CI; this test catches the
dynamic version (a lazy import inside a function body) that a static pass
cannot see, by importing ``narranexus.contracts`` into a clean module table
and asserting nothing from kernel / legacy / backend was pulled in.
"""
from __future__ import annotations

import importlib
import sys

_FORBIDDEN_PREFIXES = ("narranexus.kernel", "xyz_agent_context", "backend.")


def _purge(prefixes: tuple[str, ...]) -> None:
    for name in list(sys.modules):
        if name.startswith(prefixes) or name == "backend":
            del sys.modules[name]


def test_narranexus_package_imports():
    mod = importlib.import_module("narranexus")
    assert mod.__version__


def test_contracts_do_not_import_kernel_or_legacy():
    _purge(("narranexus",) + _FORBIDDEN_PREFIXES)
    importlib.import_module("narranexus.contracts")
    leaked = sorted(n for n in sys.modules if n.startswith(_FORBIDDEN_PREFIXES) or n == "backend")
    assert leaked == [], f"contracts pulled in non-contract modules: {leaked}"
