"""
@file_name: test_slots.py
@author: Bin Liang
@date: 2026-09-03
@description: Slot tree — declaration rules, navigation, kernel seeds, semver helpers.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import RegistryConflict, Stability, UnknownEntry
from narranexus.kernel.plugins.compat import Range, Version
from narranexus.kernel.plugins.slots import Slot, SlotTree, build_kernel_slot_tree, validate_path


def test_slot_path_grammar():
    assert validate_path("turn.act.framework") == "turn.act.framework"
    for bad in ("Turn", "turn..act", "turn.", "9x", "turn-act", ""):
        with pytest.raises(ValueError):
            validate_path(bad)


def test_many_slot_cannot_carry_a_default_and_arity_is_validated():
    with pytest.raises(ValueError, match="no single default"):
        Slot("a.b", "many", "x:Y", "p", default="q")
    with pytest.raises(ValueError, match="arity"):
        Slot("a.b", "either", "x:Y", "p")  # type: ignore[arg-type]


def test_declare_requires_parent_and_rejects_duplicates():
    tree = SlotTree()
    with pytest.raises(UnknownEntry, match="parent 'turn' is not declared"):
        tree.declare(Slot("turn.recall", "one", "x:Y", "p"))
    tree.declare(Slot("turn", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.recall", "one", "x:Y", "p", default="p"))
    with pytest.raises(RegistryConflict, match="already declared by 'p'"):
        tree.declare(Slot("turn.recall", "one", "x:Y", "q", default="q"))


def test_navigation_children_descendants_paths():
    tree = SlotTree()
    tree.declare(Slot("turn", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.act", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.act.framework", "one", "x:Y", "p", default="p"))
    tree.declare(Slot("turn.recall", "one", "x:Y", "p", default="p"))
    assert [s.path for s in tree.children("turn")] == ["turn.act", "turn.recall"]
    assert [s.path for s in tree.descendants("turn")] == ["turn.act", "turn.act.framework", "turn.recall"]
    assert tree.paths() == ("turn", "turn.act", "turn.act.framework", "turn.recall")
    assert tree.get("turn.act").parent == "turn" and tree.get("turn").parent is None
    assert "turn.act" in tree and len(tree) == 4 and list(tree)[0] == "turn"
    with pytest.raises(UnknownEntry, match="unknown slot 'nope'"):
        tree.get("nope")
    assert tree.try_get("nope") is None


def test_kernel_seed_tree_has_the_batch0_roots_and_is_docs_friendly():
    tree = build_kernel_slot_tree()
    for path in ("kernel.db", "kernel.auth", "turn.pipeline", "turn.act.framework",
                 "model.providers", "model.clients", "agent.capabilities.memory_kinds", "ui"):
        assert path in tree
    assert tree.get("kernel.auth").distribution_only is True
    assert tree.get("turn.act.framework").default == "builtin.frameworks.nexus_power"
    assert tree.get("model.providers").arity == "many"
    rows = tree.to_rows()
    assert rows == sorted(rows, key=lambda r: r["path"])
    assert all(r["stability"] == Stability.ALPHA.value for r in rows)


def test_version_parse_order_and_str():
    assert Version.parse("1.2.3") < Version.parse("1.10.0")
    assert Version.parse("1.2") == Version.parse("1.2.0")
    assert Version.parse("1.2.0-beta.1") < Version.parse("1.2.0")
    assert Version.parse("1.2.0-alpha") < Version.parse("1.2.0-beta")
    assert Version.parse("1.2.0+build.7") == Version.parse("1.2.0")
    assert str(Version.parse("1.2.0-rc.2")) == "1.2.0-rc.2"
    with pytest.raises(ValueError):
        Version.parse("v1.2.3")


@pytest.mark.parametrize(
    ("range_text", "inside", "outside"),
    [
        ("*", ["0.0.1", "9.9.9"], []),
        ("1.2.3", ["1.2.3"], ["1.2.4"]),
        (">=1.19 <2", ["1.19.0", "1.99.7"], ["1.18.9", "2.0.0"]),
        ("^1.2.3", ["1.2.3", "1.9.0"], ["1.2.2", "2.0.0"]),
        ("^0.2.1", ["0.2.1", "0.2.9"], ["0.3.0"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4"]),
        ("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0"]),
    ],
)
def test_range_contains(range_text, inside, outside):
    r = Range.parse(range_text)
    assert all(r.contains(v) for v in inside), range_text
    assert not any(r.contains(v) for v in outside), range_text
    assert str(r) == range_text.strip() or range_text == "*"


def test_range_rejects_garbage():
    with pytest.raises(ValueError):
        Range.parse(">= banana")
