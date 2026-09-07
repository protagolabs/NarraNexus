"""
@file_name: test_binding_grammar.py
@author: Bin Liang
@date: 2026-09-07
@description: One grammar for binding values (owner / name / owner:name, +/-/= verbs, lists) — referenced_plugins() — used by the distribution validator and the bind CLI, so an owner:name binding is legal everywhere the runtime accepts it; and `narranexus bind` re-validates against the BOOTED slot tree, so a user plugin's declared slot can be bound.
"""
from __future__ import annotations

from pathlib import Path

from narranexus.kernel.plugins.bindings import referenced_plugins
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.distribution import parse_distribution, resolve_distribution
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.slots import Slot

HOST = "1.19.0"


def test_referenced_plugins_understands_every_value_form():
    assert referenced_plugins("acme.x") == {"acme.x"}
    assert referenced_plugins("acme.x:section") == {"acme.x"}
    assert referenced_plugins(["+acme.a:one", "-builtin.prompts", "=acme.b,acme.c:z"]) == {"acme.a", "builtin.prompts", "acme.b", "acme.c"}
    assert referenced_plugins(("acme.a",)) == {"acme.a"}
    assert referenced_plugins(None) == set()


def test_distribution_accepts_owner_name_and_verb_forms():
    spec = parse_distribution({
        "id": "acme.app", "displayName": "Acme", "engine": ">=1.0 <2",
        "plugins": {"builtin.chat": "^1.0", "builtin.basic_info": "^1.0", "builtin.auth.local": "^1.0", "builtin.prompts": "^1.0"},
        "auth": "builtin.auth.local",
        "bindings": {"prompt.sections": ["builtin.prompts:security", "-builtin.prompts:modules"]},
    })
    res = resolve_distribution(spec, Path("."), host_version=HOST)
    assert res.ok, res.problems
    bad = parse_distribution({
        "id": "acme.app", "displayName": "Acme", "engine": ">=1.0 <2",
        "plugins": {"builtin.chat": "^1.0", "builtin.basic_info": "^1.0", "builtin.auth.local": "^1.0"},
        "auth": "builtin.auth.local",
        "bindings": {"prompt.sections": ["acme.nowhere:security"]},
    })
    res = resolve_distribution(bad, Path("."), host_version=HOST)
    assert any("acme.nowhere" in p for p in res.problems)


def test_bind_accepts_a_slot_declared_by_a_user_plugin(tmp_path: Path):
    from narranexus.cli.bindings_cli import bind, read_bindings_table

    regs = Registries(slot_tree_with_builtins())
    regs.slots.declare(Slot("acme.weather.sources", arity="many", contract="nxplugins.acme_weather:Source", owner="acme.weather"), create_namespaces=True)
    regs.registry_for("acme.weather.sources").register_contribution(Contribution("openmeteo", lambda: 1), owner="acme.x")
    regs.registry_for("prompt.assembler").register_contribution(Contribution("default", lambda: 1), owner="builtin.prompts")
    toml = tmp_path / "narranexus.toml"
    out = bind(regs, toml, "acme.weather.sources", ["acme.x:openmeteo"])
    assert out["bound"] == ["acme.x:openmeteo"]
    # and a later, unrelated bind is not broken by the user-slot binding already in the file
    bind(regs, toml, "prompt.assembler", ["builtin.prompts"])
    assert set(read_bindings_table(toml)) == {"acme.weather.sources", "prompt.assembler"}
