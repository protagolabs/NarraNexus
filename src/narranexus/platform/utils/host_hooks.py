"""
@file_name: host_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: Call a host event (contracts.events) from platform code against the process kernel registries.

Builtins implement host events through ``backend.hooks`` (registered by
``module/contributions.register_all`` at import and by the manifest loader at
boot). Platform code that fires one goes through here so the builtin
registrations are guaranteed to exist in this process regardless of import
order, and so the call site names the event, not a module.
"""
from __future__ import annotations

from typing import Any


def _hooks():
    import narranexus.platform.module_system  # noqa: F401 — registers the builtins' hooks/services (idempotent)
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES.hooks


async def call_host_hook(event: str, /, **payload: Any):
    """Run every implementation of host ``event``; returns the HookOutcome (results in registration order).

    ``event`` is positional-only so payload keys such as ``name`` never collide with it."""
    return await _hooks().caller(event).call(**payload)


__all__ = ["call_host_hook"]
