"""
@file_name: slots.py
@author: Bin Liang
@date: 2026-09-03
@description: The slot tree — every extension point the platform or a plugin declares, by path.

A *slot* is a named, contract-bearing hole. Its ``arity`` says whether exactly
one provider fills it (``one``, replaceable) or any number do (``many``,
additive). Slots form a tree by dotted path (``turn.act.framework``); the
provider of a composite slot owns the definition of its children, which is how
"replace the whole runtime" and "replace one stage inside it" coexist.

The kernel seeds the roots (``kernel.*``, ``turn.pipeline`` and the first-level
domains); everything below is declared by the plugin that provides the parent.
Declaration is fail-loud: a duplicate path or a child whose parent is not yet
declared raises, because a silently missing slot would surface much later as a
plugin that "just doesn't do anything".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Mapping

from narranexus.contracts import RegistryConflict, Stability, UnknownEntry

Arity = Literal["one", "many"]

_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def validate_path(path: str) -> str:
    if not _PATH_RE.match(path):
        raise ValueError(
            f"invalid slot path {path!r}: dotted lowercase identifiers only (e.g. 'turn.recall')"
        )
    return path


@dataclass(frozen=True)
class Slot:
    """One extension point."""

    path: str
    arity: Arity
    contract: str  # "module.path:Symbol" of the Protocol / HookSpec that fills it
    owner: str  # plugin id that declared it ("builtin.kernel" for seeded roots)
    default: str | None = None  # provider plugin id (one-arity) or None
    stability: Stability = Stability.ALPHA
    distribution_only: bool = False  # only bindable from the distribution/default layers
    doc: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_path(self.path)
        if self.arity not in ("one", "many"):
            raise ValueError(f"slot {self.path!r}: arity must be 'one' or 'many'")
        if self.arity == "many" and self.default is not None:
            raise ValueError(f"slot {self.path!r}: a many-arity slot has no single default")

    @property
    def parent(self) -> str | None:
        head, _, _ = self.path.rpartition(".")
        return head or None


class SlotTree:
    """Path-keyed registry of slots with parent/child navigation."""

    def __init__(self) -> None:
        self._slots: dict[str, Slot] = {}

    # ------------------------------------------------------------ mutation

    def declare(self, slot: Slot) -> Slot:
        if slot.path in self._slots:
            raise RegistryConflict(
                f"slot {slot.path!r} already declared by {self._slots[slot.path].owner!r}"
            )
        parent = slot.parent
        if parent is not None and parent not in self._slots:
            raise UnknownEntry(
                f"slot {slot.path!r}: parent {parent!r} is not declared; declare it first"
            )
        self._slots[slot.path] = slot
        return slot

    # -------------------------------------------------------------- lookup

    def get(self, path: str) -> Slot:
        try:
            return self._slots[path]
        except KeyError:
            raise UnknownEntry(f"unknown slot {path!r}. Known: {sorted(self._slots) or '[]'}") from None

    def try_get(self, path: str) -> Slot | None:
        return self._slots.get(path)

    def children(self, path: str) -> tuple[Slot, ...]:
        prefix = path + "."
        return tuple(
            s for p, s in sorted(self._slots.items()) if p.startswith(prefix) and "." not in p[len(prefix):]
        )

    def descendants(self, path: str) -> tuple[Slot, ...]:
        prefix = path + "."
        return tuple(s for p, s in sorted(self._slots.items()) if p.startswith(prefix))

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._slots))

    def to_rows(self) -> list[dict[str, Any]]:
        """Stable, JSON-friendly view for docs generation and the factory UI."""
        return [
            {
                "path": s.path,
                "arity": s.arity,
                "contract": s.contract,
                "owner": s.owner,
                "default": s.default,
                "stability": s.stability.value,
                "distribution_only": s.distribution_only,
                "doc": s.doc,
            }
            for s in (self._slots[p] for p in sorted(self._slots))
        ]

    def __contains__(self, path: object) -> bool:
        return path in self._slots

    def __len__(self) -> int:
        return len(self._slots)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._slots))


KERNEL_OWNER = "builtin.kernel"


def build_kernel_slot_tree() -> SlotTree:
    """The roots every process starts from (spec §6.2, batch-0 subset).

    Children that a builtin plugin declares (``turn.ingress`` … ``turn.reflect``,
    the nexus_power seams, the UI sub-points) arrive with those plugins in later
    batches; declaring them here would put their definition in the wrong owner.
    """
    tree = SlotTree()
    one = "one"
    many = "many"
    seeds: list[Slot] = [
        Slot("kernel", one, "narranexus.kernel:Kernel", KERNEL_OWNER, default=KERNEL_OWNER,
             distribution_only=True, doc="Kernel root; never bound directly."),
        Slot("kernel.db", one, "narranexus.contracts.services:DatabaseBackend", KERNEL_OWNER,
             default="builtin.kernel", distribution_only=True, doc="Database backend (sqlite | sqlite_proxy | mysql)."),
        Slot("kernel.secrets", one, "narranexus.contracts.services:SecretStore", KERNEL_OWNER,
             default="builtin.kernel", distribution_only=True, doc="Secret storage (secret_box | keychain | vault)."),
        Slot("kernel.auth", one, "narranexus.contracts.services:AuthProvider", KERNEL_OWNER,
             default="builtin.auth.local", distribution_only=True, doc="Authentication provider; distribution-level choice."),
        Slot("kernel.events", one, "narranexus.contracts.services:EventSink", KERNEL_OWNER,
             default="builtin.kernel", distribution_only=True, doc="Host event bus implementation."),
        Slot("turn", one, "narranexus.contracts.agent.pipeline:TurnPipeline", KERNEL_OWNER,
             default="builtin.turn", doc="Turn domain root."),
        Slot("turn.pipeline", one, "narranexus.contracts.agent.pipeline:TurnPipeline", KERNEL_OWNER,
             default="builtin.turn", doc="The whole turn runtime; its provider declares the stage slots."),
        Slot("turn.act", one, "narranexus.contracts.agent.pipeline:ActStrategy", KERNEL_OWNER,
             default="builtin.turn", doc="Act stage (agent loop or direct trigger)."),
        Slot("turn.act.framework", one, "narranexus.contracts.framework:AgentLoopDriver", KERNEL_OWNER,
             default="builtin.frameworks.nexus_power", doc="Agent-loop framework used by the Act stage."),
        Slot("model", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Model domain root."),
        Slot("model.providers", many, "narranexus.contracts.provider:ProviderDriver", KERNEL_OWNER,
             doc="LLM provider drivers (credential/endpoint axis)."),
        Slot("model.clients", many, "narranexus.contracts.llm_client:LlmClient", KERNEL_OWNER,
             doc="Helper-LLM protocol clients (atomic call axis)."),
        Slot("model.resolver", one, "narranexus.contracts.llm_client:ModelResolver", KERNEL_OWNER,
             default="builtin.providers", doc="Model-name resolution (the three legacy _resolve_model paths, unified in batch 1)."),
        Slot("agent", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Agent capability domain root."),
        Slot("agent.capabilities", one, "narranexus.contracts.agent.capability:CapabilitySet", KERNEL_OWNER,
             default=KERNEL_OWNER, doc="Capability namespace; children are the four capability tiers."),
        Slot("agent.capabilities.memory_kinds", many, "narranexus.contracts.memory:MemoryKindContract", KERNEL_OWNER,
             doc="Memory kinds (recall / commit / reflect participants)."),
        Slot("backend", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Backend service domain root."),
        Slot("ingress", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Ingress domain root (channels, triggers)."),
        Slot("ui", one, "narranexus.contracts.ui:Shell", KERNEL_OWNER, default="builtin.ui",
             distribution_only=True, doc="Frontend shell; distribution-level choice."),
        Slot("content", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Content-pack domain root."),
    ]
    for slot in seeds:
        tree.declare(slot)
    return tree


__all__ = ["Arity", "Slot", "SlotTree", "KERNEL_OWNER", "build_kernel_slot_tree", "validate_path"]
