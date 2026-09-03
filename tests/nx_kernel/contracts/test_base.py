"""
@file_name: test_base.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract primitives — Disposable semantics, error taxonomy, version/stability tables.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import (
    API_VERSIONS,
    STABILITY,
    BindingConflict,
    Disposable,
    DisposableStack,
    IncompatibleProvider,
    ManifestError,
    PluginError,
    RegistryConflict,
    RegistryFrozen,
    Stability,
    UnboundSlot,
    UnknownEntry,
)


def test_disposable_runs_once():
    calls: list[int] = []
    d = Disposable(lambda: calls.append(1))
    assert not d.disposed
    d.dispose()
    d.dispose()
    assert calls == [1]
    assert d.disposed


def test_disposable_stack_disposes_in_reverse_order():
    order: list[str] = []
    stack = DisposableStack()
    stack.add(Disposable(lambda: order.append("a")))
    stack.add(Disposable(lambda: order.append("b")))
    assert len(stack) == 2
    stack.dispose()
    assert order == ["b", "a"]
    assert stack.disposed
    assert len(stack) == 0


def test_disposable_stack_aggregates_failures_and_still_releases_the_rest():
    order: list[str] = []

    def boom() -> None:
        raise RuntimeError("x")

    stack = DisposableStack()
    stack.add(Disposable(lambda: order.append("first")))
    stack.add(Disposable(boom))
    with pytest.raises(ExceptionGroup) as info:
        stack.dispose()
    assert order == ["first"]
    assert [type(e) for e in info.value.exceptions] == [RuntimeError]


def test_adding_to_a_disposed_stack_disposes_immediately():
    stack = DisposableStack()
    stack.dispose()
    d = stack.add(Disposable(lambda: None))
    assert d.disposed


def test_error_hierarchy():
    for exc in (
        RegistryConflict,
        UnknownEntry,
        RegistryFrozen,
        BindingConflict,
        UnboundSlot,
        IncompatibleProvider,
        ManifestError,
    ):
        assert issubclass(exc, PluginError)
    assert issubclass(UnknownEntry, KeyError)
    assert issubclass(ManifestError, ValueError)
    assert str(UnknownEntry("no such entry 'x'")) == "no such entry 'x'"


def test_versions_and_stability_cover_the_same_kinds_and_start_alpha():
    assert set(API_VERSIONS) == set(STABILITY)
    assert API_VERSIONS
    assert all(isinstance(v, int) and v >= 0 for v in API_VERSIONS.values())
    assert all(s is Stability.ALPHA for s in STABILITY.values())
