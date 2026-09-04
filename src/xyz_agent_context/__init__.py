"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: ``xyz_agent_context`` — a one-release alias of ``narranexus.platform`` (plugin platform batch 6a, decision D8).

The domain packages moved to ``narranexus.platform`` (``module`` →
``module_system``). Anything still importing the old name — external scripts,
the deploy repo's compose entrypoints, an older plugin — keeps working for one
release: a ``sys.meta_path`` finder resolves every ``xyz_agent_context.<path>``
to the SAME module object as ``narranexus.platform.<path>`` (so monkeypatches,
``isinstance`` and singletons agree), and this package re-exports the platform
root. One ``DeprecationWarning`` per process names the replacement. The
package (and the three by-path entrypoint shims next to it) is removed in the
release after; see docs/PLUGIN_BATCH6_DEPLOY_LOCKSTEP.md for the deploy-side
switch.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings

_OLD = "xyz_agent_context"
_NEW = "narranexus.platform"
_RENAMED = {"module": "module_system"}


def target_name(fullname: str) -> str:
    """``xyz_agent_context.module.base`` → ``narranexus.platform.module_system.base``."""
    if fullname == _OLD:
        return _NEW
    rest = fullname[len(_OLD) + 1:]
    head, sep, tail = rest.partition(".")
    return f"{_NEW}.{_RENAMED.get(head, head)}{sep}{tail}"


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec):  # noqa: D401 — the target module IS the module
        return importlib.import_module(self._target)

    def exec_module(self, module) -> None:
        return None


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Resolves ``xyz_agent_context.*`` to the platform module of the same path."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD + "."):
            return None
        new = target_name(fullname)
        try:
            spec = importlib.util.find_spec(new)
        except ModuleNotFoundError:
            return None
        if spec is None:
            return None
        _warn_once()
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(new), is_package=spec.submodule_search_locations is not None)


_warned = False


def _warn_once() -> None:
    global _warned
    if not _warned:
        _warned = True
        warnings.warn(
            "xyz_agent_context is an alias of narranexus.platform (module → module_system) and is removed next release — import narranexus.platform instead",
            DeprecationWarning,
            stacklevel=3,
        )


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

from narranexus.platform import *  # noqa: E402,F401,F403 — the root re-exports
from narranexus.platform import __all__ as _platform_all  # noqa: E402
from narranexus.platform import __version__  # noqa: E402,F401

__all__ = list(_platform_all)


def __getattr__(name: str):
    import narranexus.platform as _platform

    return getattr(_platform, name)
