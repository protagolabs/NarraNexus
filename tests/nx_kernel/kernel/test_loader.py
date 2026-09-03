"""
@file_name: test_loader.py
@author: Bin Liang
@date: 2026-09-03
@description: Registries facade, builtin manifests, and the loader (builtins only, cloud fail-closed).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.contracts import ManifestError, PluginError, RegistryFrozen, UnknownEntry
from narranexus.kernel.plugins.builtins import BUILTIN_MANIFEST_DATA, builtin_manifests
from narranexus.kernel.plugins.loader import discover, load, load_order, resolve_symbol
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.slots import build_kernel_slot_tree

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "snapshots" / "golden"


def _golden_for_this_process() -> dict:
    # The provider registry is populated at import time and depends on the
    # deployment mode of THIS process (SystemDriver registers on cloud only),
    # so compare against the golden captured for the same mode.
    from narranexus.kernel.deployment import is_cloud_mode

    name = "registries_cloud.json" if is_cloud_mode() else "registries_local.json"
    return json.loads((GOLDEN_DIR / name).read_text())


# ---------------------------------------------------------------- registries


def test_registry_for_creates_per_slot_with_kind_version_and_normalizer():
    regs = Registries()
    fw = regs.registry_for("turn.pipeline.act.framework")
    assert fw is regs.registry_for("turn.pipeline.act.framework")
    fw.register("Claude_Code", lambda: 1, owner="p")
    assert fw.names() == ("claude_code",)
    assert regs.registry_for("model.providers").api_version == 0
    with pytest.raises(UnknownEntry):
        regs.registry_for("nope.slot")
    assert regs.paths() == ("model.providers", "turn.pipeline.act.framework")


def test_freeze_propagates_and_applies_to_later_registries():
    regs = Registries()
    fw = regs.registry_for("turn.pipeline.act.framework")
    regs.freeze()
    assert regs.frozen and fw.frozen
    with pytest.raises(RegistryFrozen):
        regs.registry_for("model.clients").register("x", lambda: 1, owner="p")


def test_legacy_registries_are_the_kernel_ones():
    from xyz_agent_context.agent_framework.loop.driver import FRAMEWORK_REGISTRY
    from xyz_agent_context.agent_framework.providers.driver.registry import DRIVER_REGISTRY
    from xyz_agent_context.memory.spec import MEMORY_KIND_REGISTRY

    assert FRAMEWORK_REGISTRY is KERNEL_REGISTRIES.registry_for("turn.pipeline.act.framework")
    assert DRIVER_REGISTRY is KERNEL_REGISTRIES.registry_for("model.providers")
    assert MEMORY_KIND_REGISTRY is KERNEL_REGISTRIES.registry_for("agent.capabilities.memory_kinds")


# ------------------------------------------------------------------ builtins


def test_builtin_manifests_validate_against_the_kernel_tree():
    manifests = builtin_manifests()
    assert [m.id for m in manifests] == [d["id"] for d in BUILTIN_MANIFEST_DATA]
    assert all(m.is_builtin and m.quality == "gold" for m in manifests)
    assert builtin_manifests() is manifests  # cached constant


def test_discover_is_builtins_only_and_ignores_user_registry_on_cloud(tmp_path):
    local = discover(cloud=False, user_registry_path=tmp_path / "registry.json")
    cloud = discover(cloud=True, user_registry_path=tmp_path / "registry.json")
    assert [m.id for m in local] == [m.id for m in cloud] == [d["id"] for d in BUILTIN_MANIFEST_DATA]


# -------------------------------------------------------------------- loader


def test_loading_builtins_into_a_fresh_registries_reproduces_the_snapshot():
    regs = Registries()
    report = load(regs, builtin_manifests(), role="backend")
    assert report.errors == [] and report.skipped == []
    snap = regs.snapshot()
    golden = _golden_for_this_process()
    assert sorted(snap["turn.pipeline.act.framework"]) == sorted(golden["agent_loop_frameworks"])
    assert sorted(snap["turn.pipeline.act.framework"].values()) == sorted(
        f"builtin.frameworks.{n}" for n in golden["agent_loop_frameworks"]
    )
    assert list(snap["model.providers"]) == golden["provider_drivers"]
    assert set(snap["model.providers"].values()) == {"builtin.providers"}
    assert sorted(snap["agent.capabilities.memory_kinds"]) == golden["memory_kinds"]
    assert set(snap["agent.capabilities.memory_kinds"].values()) == {"builtin.memory_kinds"}
    by_id = {p.plugin_id: p for p in report.loaded}
    assert by_id["builtin.providers"].entries == len(golden["provider_drivers"])
    assert all(p.duration_ms >= 0 for p in report.loaded)


def test_loading_twice_into_the_process_registries_is_idempotent():
    from xyz_agent_context.agent_framework import available_agent_loop_frameworks

    before = available_agent_loop_frameworks()
    report = load(KERNEL_REGISTRIES, builtin_manifests(), role="backend")
    assert report.errors == []
    assert available_agent_loop_frameworks() == before


def test_hosts_filter_skips_manifests_not_for_this_role():
    regs = Registries()
    report = load(regs, builtin_manifests(), role="mcp")
    assert sorted(report.skipped) == sorted(
        d["id"] for d in BUILTIN_MANIFEST_DATA if "mcp" not in d["hosts"]
    )
    assert "turn.pipeline.act.framework" not in regs.paths()


def _user_manifest(**overrides):
    data = {
        "id": "acme.demo",
        "version": "0.1.0",
        "displayName": "Demo",
        "provides": {"model.clients": ["tests.nx_kernel.kernel.test_loader:DEMO_CLIENTS"]},
    }
    data.update(overrides)
    return parse_manifest(data, tree=build_kernel_slot_tree())


DEMO_CLIENTS = (Contribution("demo", lambda: object()),)
NOT_A_CONTRIBUTION = 42


def test_user_manifest_registers_and_symbol_errors_are_isolated():
    regs = Registries()
    ok = _user_manifest()
    bad = _user_manifest(id="acme.broken", provides={"model.clients": ["tests.nx_kernel.kernel.test_loader:NOT_A_CONTRIBUTION"]})
    missing = _user_manifest(id="acme.missing", provides={"model.clients": ["tests.nx_kernel.kernel.test_loader:NOPE"]})
    report = load(regs, [ok, bad, missing], role="backend")
    assert regs.registry_for("model.clients").names() == ("demo",)
    assert [p.plugin_id for p in report.errors] == ["acme.broken", "acme.missing"]
    assert "expected a Contribution" in report.errors[0].error
    assert "has no attribute 'NOPE'" in report.errors[1].error


def test_builtin_failure_is_fatal_and_duplicate_ids_are_rejected():
    regs = Registries()
    broken = parse_manifest(
        {
            "id": "builtin.broken",
            "version": "1.0.0",
            "displayName": "Broken",
            "provides": {"model.clients": ["tests.nx_kernel.kernel.test_loader:NOT_A_CONTRIBUTION"]},
        },
        tree=build_kernel_slot_tree(),
        allow_builtin=True,
    )
    with pytest.raises(PluginError):
        load(regs, [broken], role="backend")
    dup = _user_manifest()
    with pytest.raises(ManifestError, match="duplicate plugin id"):
        load(regs, [dup, dup], role="backend")


def test_load_order_is_builtins_as_declared_then_users_by_id():
    b = list(builtin_manifests())
    u2 = _user_manifest(id="zeta.two")
    u1 = _user_manifest(id="alpha.one")
    ordered = load_order([u2] + b[::-1] + [u1])
    assert [m.id for m in ordered] == [m.id for m in b[::-1]] + ["alpha.one", "zeta.two"]


def test_plugin_declared_slot_is_applied_before_it_provides_into_it():
    regs = Registries()
    m = parse_manifest(
        {
            "id": "acme.sources",
            "version": "0.1.0",
            "displayName": "Sources",
            "declares": {"acme.sources.list": {"arity": "many", "contract": "x:Y"}},
            "provides": {"acme.sources.list": ["tests.nx_kernel.kernel.test_loader:DEMO_CLIENTS"]},
        },
        tree=build_kernel_slot_tree(),
    )
    report = load(regs, [m], role="backend")
    assert report.errors == []
    assert "acme.sources.list" in regs.slots and regs.registry_for("acme.sources.list").names() == ("demo",)


def test_resolve_symbol_reports_module_and_attribute_errors():
    # pytest may import this module under a different name than importlib
    # does, so compare the contribution's name rather than object identity.
    assert resolve_symbol("tests.nx_kernel.kernel.test_loader:DEMO_CLIENTS")[0].name == "demo"
    with pytest.raises(ModuleNotFoundError):
        resolve_symbol("no.such.module:X")
    with pytest.raises(PluginError, match="has no attribute 'X'"):
        resolve_symbol("tests.nx_kernel.kernel.test_loader:X")
