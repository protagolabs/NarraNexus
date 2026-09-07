"""
@file_name: test_extension_points.py
@author: Bin Liang
@date: 2026-09-04
@description: NexusPower's strategy seats are slots: defaults resolve to the loop's own classes, a plugin provider or an NX_BIND__ binding replaces one, unknown bindings fail loud, and the assembly builds every seat through them.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from narranexus.contracts._base import UnknownEntry
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus_plugins.frameworks_nexus_power.core import extension_points as ep

ALL = (ep.STOP, ep.COMPACTION, ep.PROJECTOR, ep.EXPRESSION, ep.POLICY)


def _ctx() -> ep.SeatContext:
    return ep.SeatContext(options=SimpleNamespace(expressive_tools=("reply",)), workspace="/tmp", base_messages=[], tail=lambda: "")


def test_ensure_registered_declares_the_five_seats_with_defaults():
    regs = Registries()
    ep.ensure_registered(regs)
    ep.ensure_registered(regs)  # idempotent
    for path in ALL:
        assert path in regs.slots
    assert regs.slots.get(ep.STOP).default == "no_more_actions" and regs.slots.get(ep.POLICY).arity == "many"
    assert regs.registry_for(ep.POLICY).names() == ("disallowed_tools", "workspace_confinement", "shell_confinement")


def test_defaults_build_the_loops_own_classes():
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.expression import ExpressionContract
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.stop import NoMoreActionsStop
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import ToolResultPruner
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.projector import PassthroughProjector
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
        DisallowedToolsLayer,
        ShellConfinementLayer,
        WorkspaceConfinementLayer,
    )

    regs = Registries()
    ctx = _ctx()
    assert isinstance(ep.resolve_one(ep.STOP, ctx, regs), NoMoreActionsStop)
    assert isinstance(ep.resolve_one(ep.COMPACTION, ctx, regs), ToolResultPruner)
    assert isinstance(ep.resolve_one(ep.PROJECTOR, ctx, regs), PassthroughProjector)
    assert isinstance(ep.resolve_one(ep.EXPRESSION, ctx, regs), ExpressionContract)
    layers = ep.resolve_many(ep.POLICY, ctx, regs)
    assert [type(layer) for layer in layers] == [DisallowedToolsLayer, WorkspaceConfinementLayer, ShellConfinementLayer]


def test_a_plugin_provider_plus_binding_replaces_a_seat(monkeypatch):
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.stop import NoMoreActionsStop

    class AlwaysStop:
        async def should_stop(self, step_calls, ledger):
            return True

    regs = Registries()
    ep.ensure_registered(regs)
    regs.registry_for(ep.STOP).register_contribution(Contribution("always", lambda: (lambda ctx: AlwaysStop())), owner="acme.stops")
    assert isinstance(ep.resolve_one(ep.STOP, _ctx(), regs), NoMoreActionsStop)  # providing alone does not rebind
    monkeypatch.setenv("NX_BIND__builtin__frameworks__nexus_power__stop", "always")
    assert isinstance(ep.resolve_one(ep.STOP, _ctx(), regs), AlwaysStop)
    assert ep.bound_provider(ep.STOP, regs) == "always"


def test_many_seat_binding_selects_and_orders(monkeypatch):
    regs = Registries()
    ep.ensure_registered(regs)
    regs.registry_for(ep.POLICY).register_contribution(Contribution("audit", lambda: (lambda ctx: "AUDIT")), owner="acme.audit")
    assert ep.bound_providers(ep.POLICY, regs)[-1] == "audit"  # appended in registration order
    monkeypatch.setenv("NX_BIND__builtin__frameworks__nexus_power__policy", "audit,disallowed_tools")
    assert ep.bound_providers(ep.POLICY, regs) == ("audit", "disallowed_tools")
    assert ep.resolve_many(ep.POLICY, _ctx(), regs)[0] == "AUDIT"


def test_unknown_binding_fails_loud(monkeypatch):
    monkeypatch.setenv("NX_BIND__builtin__frameworks__nexus_power__compaction", "nope")
    with pytest.raises(UnknownEntry):
        ep.resolve_one(ep.COMPACTION, _ctx(), Registries())


def test_manifest_and_slot_tree_agree():
    from narranexus.kernel.plugins.builtins import builtin_manifests, slot_tree_with_builtins

    tree = slot_tree_with_builtins()
    for path, arity, contract, default, _ in ep.DECLARED_SLOTS:
        slot = tree.get(path)
        assert (slot.arity, slot.contract, slot.default) == (arity, contract, default)
    manifest = next(m for m in builtin_manifests() if m.id == "builtin.frameworks.nexus_power")
    assert set(manifest.declares) == set(ALL) and set(ALL) <= set(manifest.provides)


def test_booting_the_backend_role_loads_the_seat_providers(tmp_path, monkeypatch):
    from narranexus.hosts.boot import boot
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    regs = Registries()
    boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=RegistryStore(path=home / "registry.json", lkg=home / "lkg.json"))
    assert regs.registry_for(ep.STOP).names() == ("no_more_actions",)
    assert regs.registry_for(ep.POLICY).names() == ("disallowed_tools", "workspace_confinement", "shell_confinement")


def test_assembly_resolves_every_seat_through_the_extension_points():
    from narranexus_plugins.frameworks_nexus_power.core import assembly

    src = inspect.getsource(assembly)
    for seat in ("ep.EXPRESSION", "ep.POLICY", "ep.PROJECTOR", "ep.COMPACTION", "ep.STOP"):
        assert f"resolve_one({seat}" in src or f"resolve_many({seat}" in src, seat
    for direct in ("ExpressionContract(", "ToolResultPruner(", "PassthroughProjector("):
        assert direct not in src, direct


def test_safety_layers_cannot_be_removed_by_a_binding():
    """The three builtin safety layers are mandatory: a binding that names only a custom layer appends it, and an empty engine denies."""
    from narranexus_plugins.frameworks_nexus_power.core import assembly
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
        DisallowedToolsLayer,
        PolicyEngine,
        ShellConfinementLayer,
        WorkspaceConfinementLayer,
    )

    class CustomLayer:
        def check(self, call, ctx):
            raise AssertionError("not called in this test")

    custom = CustomLayer()
    layers = assembly._with_mandatory_layers((custom,))
    assert [type(x) for x in layers] == [DisallowedToolsLayer, WorkspaceConfinementLayer, ShellConfinementLayer, CustomLayer]
    # a bound duplicate of a mandatory type is not repeated
    assert [type(x) for x in assembly._with_mandatory_layers((ShellConfinementLayer(), custom))][:3] == [DisallowedToolsLayer, WorkspaceConfinementLayer, ShellConfinementLayer]
    assert len(assembly._with_mandatory_layers((ShellConfinementLayer(), custom))) == 4
    empty = PolicyEngine(())
    decision = empty.check(type("Call", (), {"name": "x", "arguments": {}})(), None)
    assert not decision.allowed and "fail-closed" in decision.reason
