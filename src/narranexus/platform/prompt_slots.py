"""
@file_name: prompt_slots.py
@author: Bin Liang
@date: 2026-09-07
@description: The platform's seam to the prompt slots: ``ensure_registered`` has the kernel register the builtin.prompts contributions when the slots are empty (lazy, never at import), ``sections_for`` / ``assembler_for`` hand back the bound providers through ``kernel.plugins.bound`` so a distribution's or narranexus.toml's binding decides the order, the drops and the assembler.
"""
from __future__ import annotations

from typing import Any

from narranexus.contracts.prompt import PromptAssembler, PromptSectionProvider
from narranexus.kernel.plugins.bound import bound_entries, bound_entry
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

SECTIONS_SLOT = "prompt.sections"
ASSEMBLER_SLOT = "prompt.assembler"


def ensure_registered(registries: Any = None) -> None:
    regs = registries or KERNEL_REGISTRIES
    from narranexus.kernel.plugins.builtins import register_builtin_provides

    for slot in (SECTIONS_SLOT, ASSEMBLER_SLOT):
        if not regs.registry_for(slot).names():
            register_builtin_provides(slot, regs)


def sections_for(registries: Any = None) -> list[PromptSectionProvider]:
    """The section providers in effect: binding order when bound, else declared ``order``."""
    regs = registries or KERNEL_REGISTRIES
    ensure_registered(regs)
    entries = bound_entries(regs, SECTIONS_SLOT)
    providers = [e.factory() for e in entries]
    resolved = getattr(regs, "bindings", None)
    if resolved is None or SECTIONS_SLOT not in resolved.many or not resolved.many[SECTIONS_SLOT].providers:
        providers.sort(key=lambda p: getattr(p, "order", 100))
    return providers


def assembler_for(registries: Any = None) -> PromptAssembler:
    regs = registries or KERNEL_REGISTRIES
    ensure_registered(regs)
    return bound_entry(regs, ASSEMBLER_SLOT).factory()


__all__ = ["ASSEMBLER_SLOT", "SECTIONS_SLOT", "assembler_for", "ensure_registered", "sections_for"]
