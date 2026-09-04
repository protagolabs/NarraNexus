"""
@file_name: extension_points.py
@author: Bin Liang
@date: 2026-09-04
@description: NexusPower's own extension points — the loop's strategy seats as slots other plugins can fill.

The pipeline has three vertical grains (spec §484): platform stages → the
act-stage framework (a Port) → the seams inside a framework. This module is
the third grain for NexusPower: five of its protocols are declared as slots
under the plugin's namespace (``builtin.frameworks.nexus_power.<seat>``),
the loop's default implementations are the default providers, and any plugin
may provide another implementation (``provides: {"builtin.frameworks.nexus_power.stop": [...]}``)
or bind one through configuration (``NX_BIND__builtin__frameworks__nexus_power__stop=<provider>``).

A provider is ``Callable[[SeatContext], impl]``: it receives what the
assembly knows about the turn and returns the seat implementation.
``resolve_one`` / ``resolve_many`` apply the binding (env > slot default) and
build the seat; unknown providers fail loud (a bound name that nobody
provides is a configuration error, never a silent fallback).

Registration happens twice on purpose: the builtin manifest names these
contributions for booted hosts, and ``ensure_registered`` registers them in
any process that runs the loop without a plugin boot (the executor
subprocess runner), so the defaults are always there. Third-party seat
providers reach the loop when the plugin is loaded in the process running
it (a booted host).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.slots import Slot

NAMESPACE = "builtin.frameworks.nexus_power"
STOP = f"{NAMESPACE}.stop"
COMPACTION = f"{NAMESPACE}.compaction"
PROJECTOR = f"{NAMESPACE}.projector"
EXPRESSION = f"{NAMESPACE}.expression"
POLICY = f"{NAMESPACE}.policy"

_PROTOCOLS = "narranexus_plugins.frameworks_nexus_power.core.contracts.protocols"

# (path, arity, contract, default provider, doc) — the manifest `declares` and the slot tree agree on these.
DECLARED_SLOTS: tuple[tuple[str, str, str, Optional[str], str], ...] = (
    (STOP, "one", f"{_PROTOCOLS}:StopPolicy", "no_more_actions", "When the loop ends a turn (default: the model made no more tool calls)."),
    (COMPACTION, "one", f"{_PROTOCOLS}:CompactionPolicy", "tool_result_pruner", "How the ledger is compacted when the context fills up."),
    (PROJECTOR, "one", f"{_PROTOCOLS}:ContextProjector", "passthrough", "How the ledger becomes the provider message list each step."),
    (EXPRESSION, "one", f"{_PROTOCOLS}:ExpressionPolicy", "contract", "Which tools count as the agent speaking (expressive tools) and how their text is tagged."),
    (POLICY, "many", f"{_PROTOCOLS}:PolicyLayer", None, "Tool-call policy layers, checked in registration order (disallowed tools, workspace confinement, shell confinement, ...)."),
)


@dataclass(frozen=True)
class SeatContext:
    """What a seat provider may look at while building its implementation."""

    options: Any  # TurnOptions
    workspace: str
    tool_context: Any = None  # ToolContext
    profile: Any = None  # ProviderProfile
    base_messages: list[Any] = field(default_factory=list)  # projector input (harness-inserted)
    tail: Optional[Callable[[], str]] = None  # projector: the per-step tail renderer


def _stop_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.stop import NoMoreActionsStop

    return NoMoreActionsStop()


def _compaction_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import ToolResultPruner

    return ToolResultPruner()


def _projector_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.projector import PassthroughProjector

    return PassthroughProjector(ctx.base_messages, ctx.tail or (lambda: ""))


def _expression_default(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.expression import ExpressionContract

    return ExpressionContract(ctx.options.expressive_tools)


def _disallowed_tools(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import DisallowedToolsLayer

    return DisallowedToolsLayer()


def _workspace_confinement(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import WorkspaceConfinementLayer

    return WorkspaceConfinementLayer()


def _shell_confinement(ctx: SeatContext):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import ShellConfinementLayer

    return ShellConfinementLayer()


# One-arity seats are provided as a single Contribution (the manifest names the
# symbol directly); the many-arity policy seat as a tuple.
STOP_DEFAULT = Contribution("no_more_actions", lambda: _stop_default)
COMPACTION_DEFAULT = Contribution("tool_result_pruner", lambda: _compaction_default)
PROJECTOR_DEFAULT = Contribution("passthrough", lambda: _projector_default)
EXPRESSION_DEFAULT = Contribution("contract", lambda: _expression_default)
STOP_POLICIES = (STOP_DEFAULT,)
COMPACTION_POLICIES = (COMPACTION_DEFAULT,)
PROJECTORS = (PROJECTOR_DEFAULT,)
EXPRESSION_POLICIES = (EXPRESSION_DEFAULT,)
# Order is the check order; the same order the assembly always used.
POLICY_LAYERS = (
    Contribution("disallowed_tools", lambda: _disallowed_tools),
    Contribution("workspace_confinement", lambda: _workspace_confinement),
    Contribution("shell_confinement", lambda: _shell_confinement),
)

PROVIDERS: dict[str, tuple[Contribution[Any], ...]] = {
    STOP: STOP_POLICIES,
    COMPACTION: COMPACTION_POLICIES,
    PROJECTOR: PROJECTORS,
    EXPRESSION: EXPRESSION_POLICIES,
    POLICY: POLICY_LAYERS,
}


def slots() -> tuple[Slot, ...]:
    return tuple(
        Slot(path=path, arity=arity, contract=contract, owner=NAMESPACE, default=default, doc=doc)  # type: ignore[arg-type]
        for path, arity, contract, default, doc in DECLARED_SLOTS
    )


def _registries(registries: Any):
    if registries is not None:
        return registries
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES


def ensure_registered(registries: Any = None) -> None:
    """Declare the seats and register the default providers (idempotent; a booted host already has them from the manifest)."""
    regs = _registries(registries)
    for slot in slots():
        if slot.path not in regs.slots:
            regs.slots.declare(slot, create_namespaces=True)
    for path, contributions in PROVIDERS.items():
        registry = regs.registry_for(path)
        for contribution in contributions:
            if contribution.name not in registry:
                registry.register_contribution(contribution, owner=NAMESPACE)


def _env_binding(path: str) -> Optional[str | list[str]]:
    from narranexus.kernel.plugins.bindings import parse_env

    return parse_env(os.environ).entries.get(path)


def bound_provider(path: str, registries: Any = None) -> str:
    """The provider name bound to a one-arity seat: ``NX_BIND__…`` env over the slot default."""
    regs = _registries(registries)
    ensure_registered(regs)
    value = _env_binding(path)
    if isinstance(value, list):
        raise ValueError(f"{path}: one-arity seat bound with a list {value!r}")
    if value:
        return value
    default = regs.slots.get(path).default
    if default is None:
        raise ValueError(f"{path}: no binding and no default provider")
    return default


def bound_providers(path: str, registries: Any = None) -> tuple[str, ...]:
    """The provider names for a many-arity seat: the env list, else every registered provider in order."""
    regs = _registries(registries)
    ensure_registered(regs)
    value = _env_binding(path)
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if value:
        return tuple(value)
    return regs.registry_for(path).names()


def resolve_one(path: str, ctx: SeatContext, registries: Any = None) -> Any:
    """Build the bound implementation of a one-arity seat (unknown provider → UnknownEntry, fail loud)."""
    regs = _registries(registries)
    name = bound_provider(path, regs)
    return regs.registry_for(path).get(name)(ctx)


def resolve_many(path: str, ctx: SeatContext, registries: Any = None) -> tuple[Any, ...]:
    """Build every bound implementation of a many-arity seat, in binding order."""
    regs = _registries(registries)
    registry = regs.registry_for(path)
    return tuple(registry.get(name)(ctx) for name in bound_providers(path, regs))


__all__ = [
    "COMPACTION",
    "COMPACTION_DEFAULT",
    "COMPACTION_POLICIES",
    "DECLARED_SLOTS",
    "EXPRESSION",
    "EXPRESSION_DEFAULT",
    "EXPRESSION_POLICIES",
    "NAMESPACE",
    "POLICY",
    "POLICY_LAYERS",
    "PROJECTOR",
    "PROJECTORS",
    "PROJECTOR_DEFAULT",
    "PROVIDERS",
    "STOP",
    "STOP_DEFAULT",
    "STOP_POLICIES",
    "SeatContext",
    "bound_provider",
    "bound_providers",
    "ensure_registered",
    "resolve_many",
    "resolve_one",
    "slots",
]
