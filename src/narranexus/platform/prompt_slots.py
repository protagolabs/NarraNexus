"""
@file_name: prompt_slots.py
@author: Bin Liang
@date: 2026-09-07
@description: The platform's seam to the prompt slots: ``sections_for`` / ``assembler_for`` hand back the bound providers through ``kernel.plugins.bound`` so a distribution's or narranexus.toml's binding decides the order, the drops and the assembler.
"""
from __future__ import annotations

from typing import Any

from narranexus.contracts.prompt import PromptAssembler, PromptSectionProvider
from narranexus.kernel.plugins.bound import bound_entries, bound_entry
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

SECTIONS_SLOT = "prompt.sections"
ASSEMBLER_SLOT = "prompt.assembler"


def sections_for(registries: Any = None) -> list[PromptSectionProvider]:
    """The section providers in effect: binding order when bound, else declared ``order``.

    The registries are populated by the host boot (builtin.prompts' manifest);
    an empty slot answers an empty prompt, never a silently re-registered one."""
    regs = registries or KERNEL_REGISTRIES
    entries = bound_entries(regs, SECTIONS_SLOT)
    providers = [e.factory() for e in entries]
    resolved = getattr(regs, "bindings", None)
    if resolved is None or SECTIONS_SLOT not in resolved.many or not resolved.many[SECTIONS_SLOT].providers:
        providers.sort(key=lambda p: getattr(p, "order", 100))
    return providers


def assembler_for(registries: Any = None) -> PromptAssembler:
    regs = registries or KERNEL_REGISTRIES
    return bound_entry(regs, ASSEMBLER_SLOT).factory()


__all__ = ["ASSEMBLER_SLOT", "SECTIONS_SLOT", "assembler_for", "sections_for"]
