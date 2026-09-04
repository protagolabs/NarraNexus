"""
@file_name: provider.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract test base for provider driver classes.

Subclass in a test module and set ``driver_cls``. The checks are structural
(class shape), so they need no credentials and no network.
"""
from __future__ import annotations

import inspect
from typing import ClassVar

from narranexus.contracts.provider import ProviderDriver


class ProviderDriverContractTests:
    """Executable definition of the ``provider`` contract."""

    driver_cls: ClassVar[type | None] = None

    def _cls(self) -> type:
        cls = type(self).driver_cls
        assert cls is not None, "set driver_cls on the subclass"
        return cls

    def test_driver_type_is_a_non_empty_lowercase_key(self):
        key = self._cls().driver_type()
        assert isinstance(key, str) and key and key == key.lower()

    def test_class_exposes_every_contract_method(self):
        for name in ProviderDriver.__protocol_attrs__:  # type: ignore[attr-defined]
            if name == "card":
                continue
            assert hasattr(self._cls(), name), f"missing {name}"

    def test_probe_is_a_coroutine_function(self):
        assert inspect.iscoroutinefunction(self._cls().probe)

    def test_models_is_synchronous(self):
        assert not inspect.iscoroutinefunction(self._cls().models)
