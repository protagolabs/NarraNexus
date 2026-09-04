"""
@file_name: test_legacy_package_shim.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 6a (D8) — `xyz_agent_context` is a one-release alias of `narranexus.platform`: every old dotted path resolves to the SAME module object (module → module_system), the root re-exports match, one DeprecationWarning is emitted, and the by-path / -m entrypoint shims delegate to the moved main().
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_old_paths_alias_the_platform_modules():
    import narranexus.platform.module_system.base as new_base
    import narranexus.platform.schema.hook_schema as new_hook

    import xyz_agent_context

    xyz_agent_context._warned = False  # another test may already have paid the once-per-process warning
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old_base = importlib.import_module("xyz_agent_context.module.base")
        old_hook = importlib.import_module("xyz_agent_context.schema.hook_schema")
        importlib.import_module("xyz_agent_context.utils.db.database")
    assert old_base is new_base and old_hook is new_hook
    assert old_base.XYZBaseModule is new_base.XYZBaseModule
    assert any(issubclass(w.category, DeprecationWarning) and "narranexus.platform" in str(w.message) for w in caught)

    from narranexus.platform import AgentRuntime, XYZBaseModule

    assert xyz_agent_context.XYZBaseModule is XYZBaseModule and xyz_agent_context.AgentRuntime is AgentRuntime
    assert sys.modules["xyz_agent_context.module.base"] is new_base
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("xyz_agent_context.no_such_package")


def test_warning_is_emitted_once_per_process():
    code = (
        "import warnings; warnings.simplefilter('always'); seen=[]\n"
        "with warnings.catch_warnings(record=True) as w:\n"
        "    import xyz_agent_context.narrative, xyz_agent_context.schema, xyz_agent_context.module.base\n"
        "print(sum(1 for x in w if x.category is DeprecationWarning and 'alias' in str(x.message)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, check=True).stdout.strip()
    assert out == "1"


def test_entrypoint_shims_delegate_to_the_moved_mains():
    from narranexus.platform.module_system import module_runner, run_worker_supervisor
    from narranexus.platform.utils.db import sqlite_proxy_server

    for shim, target in (
        ("xyz_agent_context.module.module_runner", module_runner),
        ("xyz_agent_context.module.run_worker_supervisor", run_worker_supervisor),
        ("xyz_agent_context.utils.db.sqlite_proxy_server", sqlite_proxy_server),
    ):
        assert importlib.import_module(shim).main is target.main
    # the by-path shim the deploy compose runs
    path_shim = REPO / "src/xyz_agent_context/module/module_runner.py"
    assert path_shim.exists() and "narranexus.platform.module_system.module_runner import main" in path_shim.read_text()
    assert module_runner.main(["help"]) == 0
