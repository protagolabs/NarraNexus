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
import json
import subprocess
import sys

_PROBE = """
import importlib, json, sys
importlib.import_module("narranexus.contracts")
forbidden = ("narranexus.kernel", "xyz_agent_context", "backend")
leaked = sorted(n for n in sys.modules if n.startswith(forbidden))
print(json.dumps(leaked))
"""


def test_narranexus_package_imports():
    mod = importlib.import_module("narranexus")
    assert mod.__version__


def test_contracts_do_not_import_kernel_or_legacy():
    # A fresh interpreter: purging sys.modules in-process would re-import the
    # legacy package later and break class identity for every other test.
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True, timeout=120
    )
    leaked = json.loads(out.stdout.strip().splitlines()[-1])
    assert leaked == [], f"contracts pulled in non-contract modules: {leaked}"
