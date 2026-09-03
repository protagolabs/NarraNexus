"""
@file_name: memory.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract test base for memory-kind specs.

Subclass in a test module and set ``spec_factory`` to a zero-arg callable
returning the spec object.
"""
from __future__ import annotations

from typing import Any, Callable, ClassVar

from narranexus.contracts.memory import MemoryKindContract


class MemoryKindContractTests:
    """Executable definition of the ``memory`` contract."""

    # ``Any`` for the same reason as FrameworkDriverContractTests.driver_factory.
    spec_factory: ClassVar[Any] = None

    def _spec(self) -> MemoryKindContract:
        factory: Callable[[], MemoryKindContract] | None = type(self).spec_factory
        assert factory is not None, "set spec_factory on the subclass"
        return factory()

    def test_satisfies_structural_protocol(self):
        assert isinstance(self._spec(), MemoryKindContract)

    def test_kind_is_a_non_empty_identifier(self):
        kind = self._spec().kind
        assert isinstance(kind, str) and kind.isidentifier()

    def test_passive_is_a_bool(self):
        assert isinstance(self._spec().passive, bool)
