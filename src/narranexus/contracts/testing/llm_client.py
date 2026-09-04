"""
@file_name: llm_client.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract test base for helper-LLM clients.

Subclass in a test module and set ``client_cls``. The checks compare the
implementation's ``llm_function`` / ``llm_stream`` signatures with the
``LlmClient`` Protocol so a drifted parameter name fails here, not at a call
site three layers away.
"""
from __future__ import annotations

import inspect
from typing import ClassVar

from narranexus.contracts.llm_client import LlmClient


def _param_names(fn) -> list[str]:
    return [p for p in inspect.signature(fn).parameters if p != "self"]


class LlmClientContractTests:
    """Executable definition of the ``llm_client`` contract."""

    client_cls: ClassVar[type | None] = None

    def _cls(self) -> type:
        cls = type(self).client_cls
        assert cls is not None, "set client_cls on the subclass"
        return cls

    def test_llm_function_is_a_coroutine_with_the_contract_parameters(self):
        fn = self._cls().llm_function
        assert inspect.iscoroutinefunction(fn)
        assert _param_names(fn) == _param_names(LlmClient.llm_function)

    def test_llm_stream_is_an_async_generator_with_the_contract_parameters(self):
        fn = self._cls().llm_stream
        assert inspect.isasyncgenfunction(fn)
        assert _param_names(fn) == _param_names(LlmClient.llm_stream)

    def test_class_satisfies_the_structural_protocol(self):
        assert all(hasattr(self._cls(), name) for name in ("llm_function", "llm_stream"))
