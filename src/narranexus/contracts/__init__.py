"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: The public API surface of the plugin platform.

Plugins import from here and nowhere else. Two tables govern evolution
(docs/API_POLICY.md):

``API_VERSIONS``   integer contract version per kind; only ever increases, and a
                   breaking change to a kind's Protocol MUST bump it.
``STABILITY``      stability level per kind; everything is alpha until batch 6 of
                   the plugin-platform roadmap marks the surface stable.
"""
from __future__ import annotations

from narranexus.contracts._base import (
    BindingConflict,
    CancellationSignal,
    Disposable,
    DisposableStack,
    IncompatibleProvider,
    ManifestError,
    PluginError,
    RegistryConflict,
    RegistryFrozen,
    Stability,
    UnboundSlot,
    UnknownEntry,
)

API_VERSIONS: dict[str, int] = {
    "framework": 0,
    "provider": 0,
    "llm_client": 0,
    "memory": 0,
    "events": 0,
}

STABILITY: dict[str, Stability] = {kind: Stability.ALPHA for kind in API_VERSIONS}

__all__ = [
    "API_VERSIONS",
    "STABILITY",
    "Stability",
    "PluginError",
    "RegistryConflict",
    "UnknownEntry",
    "RegistryFrozen",
    "BindingConflict",
    "UnboundSlot",
    "IncompatibleProvider",
    "ManifestError",
    "Disposable",
    "DisposableStack",
    "CancellationSignal",
]
