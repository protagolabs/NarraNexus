"""
@file_name: prompt.py
@author: Bin Liang
@date: 2026-09-07
@description: The prompt contracts (kind ``prompt``): a system prompt is a list of SECTIONS rendered by providers filling the many-slot ``prompt.sections``, joined by the ASSEMBLER bound to the one-slot ``prompt.assembler``. A distribution or ``narranexus.toml`` can reorder / drop sections and replace the assembler without touching the platform; a plugin can add a section with a character budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Optional, Protocol, Sequence, runtime_checkable


@dataclass
class PromptContext:
    """What a section sees when it renders for one turn.

    ``runtime`` is the host's context-building object (the platform's
    ContextRuntime today) — sections that need its helpers (temporal block,
    narrative prompt, module instruction formatting) call it; a section that
    renders pure text ignores it. ``part_sizes`` and ``meta`` are the
    diagnostics the host logs after assembly; a section may add to them."""

    agent_id: str
    user_id: Optional[str]
    ctx_data: Any
    narrative_list: Sequence[Any]
    selected_events: Sequence[Any]
    module_instructions: Sequence[Any]
    db: Any
    runtime: Any
    deployment_mode: str = "local"
    part_sizes: MutableMapping[str, int] = field(default_factory=dict)
    meta: MutableMapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedSection:
    """One rendered section as handed to the assembler (empty text = skipped)."""

    id: str
    owner: str
    order: int
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@runtime_checkable
class PromptSectionProvider(Protocol):
    """Slot ``prompt.sections`` (many): renders one section of the system prompt.

    ``order`` sorts sections when no binding orders them (lower first);
    ``budget_chars`` is the section's declared maximum (0 = unbounded) — the
    assembler reports overruns, it does not truncate silently."""

    id: str
    order: int
    budget_chars: int

    async def render(self, ctx: PromptContext) -> Optional[str]: ...


@runtime_checkable
class PromptAssembler(Protocol):
    """Slot ``prompt.assembler`` (one): joins rendered sections into the final system prompt."""

    async def assemble(self, sections: Sequence[RenderedSection], ctx: PromptContext) -> str: ...


def budget_report(sections: Sequence[RenderedSection], budgets: Mapping[str, int]) -> list[str]:
    """Sections whose rendered size exceeds their declared budget (id: chars > budget)."""
    return [f"{s.id}: {s.chars} > {budgets[s.id]}" for s in sections if budgets.get(s.id, 0) and s.chars > budgets[s.id]]


__all__ = ["PromptAssembler", "PromptContext", "PromptSectionProvider", "RenderedSection", "budget_report"]
