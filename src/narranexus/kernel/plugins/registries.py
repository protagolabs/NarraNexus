"""
@file_name: registries.py
@author: Bin Liang
@date: 2026-09-03
@description: The ``Registries`` facade — one ``Registry`` per slot path, the hook registry, the slot tree.

Platform code takes its registries from here instead of building private
dicts, so "who provides what" is answerable in one place and the loader has a
single object to populate and freeze. The registry for a slot is created on
first request with the contract version of the slot's kind and any
kind-specific key normalisation (framework names are case-insensitive).

``KERNEL_REGISTRIES`` is the process-wide instance; tests build their own
``Registries`` to load manifests into a clean slate.
"""
from __future__ import annotations

from typing import Any, Callable

from narranexus.contracts import API_VERSIONS
from narranexus.kernel.plugins.hooks import HookRegistry
from narranexus.kernel.plugins.registry import Registry
from narranexus.kernel.plugins.slots import SlotTree, build_kernel_slot_tree

# slot path -> contract kind (drives api_version on the registry)
SLOT_KINDS: dict[str, str] = {
    "turn.pipeline.act.framework": "framework",
    "model.providers": "provider",
    "model.clients": "llm_client",
    "agent.capabilities.memory_kinds": "memory",
    "agent.capabilities.tools": "tool",
    "agent.capabilities.context_providers": "context_provider",
    "agent.capabilities.modules": "module",
    "agent.capabilities.data_access": "data_access",
    "turn.profiles": "pipeline_profile",
    "turn.pipeline.ingress": "stage_strategy",
    "turn.pipeline.recall": "stage_strategy",
    "turn.pipeline.compose": "stage_strategy",
    "turn.pipeline.assemble": "stage_strategy",
    "turn.pipeline.act": "stage_strategy",
    "turn.pipeline.commit": "stage_strategy",
    "turn.pipeline.reflect": "stage_strategy",
    "agent.capabilities.mcp_servers": "mcp_server",
    "backend.routes": "route",
    "backend.tables": "table",
    "backend.workers": "worker",
    "ingress.triggers": "trigger",
    "backend.settings": "settings",
    "backend.hooks": "hook",
    "ui.themes": "theme",
    "content.bundles": "bundle",
    "content.skills": "skill",
}

# slot path -> key normalisation
_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "turn.pipeline.act.framework": lambda s: s.strip().lower(),
}


class Registries:
    """All registries of one process, keyed by slot path."""

    def __init__(self, slots: SlotTree | None = None) -> None:
        self.slots: SlotTree = slots if slots is not None else build_kernel_slot_tree()
        self.hooks: HookRegistry = HookRegistry()
        # The host hook vocabulary is the kernel's: every process declares the
        # host events (contracts.events) and the fourteen stage hooks
        # (contracts.agent.events) so a plugin's ``backend.hooks`` can target
        # them without the host having to remember to declare each one.
        from narranexus.contracts.agent.events import STAGE_HOOKS
        from narranexus.contracts.events import HOST_EVENTS, host_event_params
        from narranexus.kernel.plugins.hooks import HookSpec

        for name in HOST_EVENTS:
            self.hooks.declare(HookSpec(name, host_event_params(name), doc=f"host event {name}"))
        for name, (params, firstresult) in STAGE_HOOKS.items():
            self.hooks.declare(HookSpec(name, params, firstresult=firstresult, doc=f"stage hook {name}"))
        self._by_path: dict[str, Registry[Any]] = {}
        self._frozen = False
        # Named services plugins expose to each other and to the platform
        # (see kernel/plugins/service_refs.py); one locator per process, so a
        # builtin's service and a user plugin's activate(ctx) share it.
        from narranexus.kernel.plugins.services import ServiceLocator

        self.services: ServiceLocator = ServiceLocator()

    def registry_for(self, path: str) -> Registry[Any]:
        """The registry backing ``path`` (created on first use; the slot must exist)."""
        reg = self._by_path.get(path)
        if reg is None:
            self.slots.get(path)  # UnknownEntry if the slot is not declared
            kind = SLOT_KINDS.get(path)
            reg = Registry(
                path,
                api_version=API_VERSIONS[kind] if kind else 0,
                normalize=_NORMALIZERS.get(path),
            )
            if self._frozen:
                reg.freeze()
            self._by_path[path] = reg
        return reg

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_path))

    def freeze(self) -> None:
        self._frozen = True
        for reg in self._by_path.values():
            reg.freeze()

    @property
    def frozen(self) -> bool:
        return self._frozen

    def remove_owner(self, owner: str) -> int:
        """Remove ``owner``'s contributions from every registry and block its hooks (disabled builtin)."""
        removed = sum(reg.remove_owner(owner) for reg in self._by_path.values())
        removed += self.hooks.block(owner)
        removed += self.services.release_owner(owner)
        return removed

    def snapshot(self) -> dict[str, dict[str, str]]:
        """slot path -> {entry name -> owner}; deterministic, for reports and tests."""
        return {
            path: {e.name: e.owner for e in self._by_path[path].entries()}
            for path in sorted(self._by_path)
        }


KERNEL_REGISTRIES = Registries()

__all__ = ["SLOT_KINDS", "Registries", "KERNEL_REGISTRIES"]
