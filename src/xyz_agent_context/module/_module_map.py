"""
@file_name: _module_map.py
@author: Bin Liang
@date: 2026-09-04
@description: ``ModuleMapView`` — the read-only ``MODULE_MAP`` mapping (name → module class) backed by the kernel ``agent.capabilities.modules`` registry.

Every consumer that did ``MODULE_MAP[name]`` / ``in`` / ``.items()`` keeps
working; what changed is where the truth lives. Builds are cached per
registry generation so hot paths pay a dict lookup, and a disabled builtin
(removed from the registry at boot) is simply absent.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterator


class ModuleMapView(Mapping[str, type]):
    def __init__(self, slot: str, registries: Any = None) -> None:
        self._slot = slot
        self._registries = registries
        self._cache: dict[str, type] | None = None
        self._cache_key: tuple[str, ...] | None = None

    def _registry(self):
        if self._registries is None:
            from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

            return KERNEL_REGISTRIES.registry_for(self._slot)
        return self._registries.registry_for(self._slot)

    def _build(self) -> dict[str, type]:
        registry = self._registry()
        key = registry.names()
        if self._cache is not None and key == self._cache_key:
            return self._cache
        built: dict[str, type] = {}
        for entry in registry.entries():
            try:
                built[entry.name] = entry.factory()
            except Exception as exc:  # noqa: BLE001 — a module whose import fails is absent, not fatal
                from loguru import logger

                logger.warning(f"[modules] {entry.owner}: module {entry.name!r} failed to import: {exc}")
        self._cache, self._cache_key = built, key
        return built

    def meta(self, name: str) -> dict[str, Any]:
        registry = self._registry()
        for entry in registry.entries():
            if entry.name == name:
                return dict(entry.meta)
        raise KeyError(name)

    def owner_of(self, name: str) -> str:
        return self._registry().owner_of(name)

    def __getitem__(self, name: str) -> type:
        return self._build()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._build())

    def __len__(self) -> int:
        return len(self._build())

    def __contains__(self, name: object) -> bool:
        return name in self._build()

    def __repr__(self) -> str:
        return f"ModuleMapView({list(self._build())})"


__all__ = ["ModuleMapView"]
