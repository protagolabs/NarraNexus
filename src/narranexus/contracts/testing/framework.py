"""
@file_name: framework.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract test base for agent-loop framework drivers.

Subclass in a test module and set ``driver_factory`` to a zero-arg callable
returning a driver instance. Every builtin framework and every third-party one
runs exactly these checks.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, ClassVar

from narranexus.contracts.framework import CAPABILITY_VOCABULARY, AgentLoopDriver


class FrameworkDriverContractTests:
    """Executable definition of the ``framework`` contract."""

    # ``Any``: pyright would otherwise try to bind a plain callable stored on the
    # class as a method. Subclasses set a zero-arg callable (a staticmethod works).
    driver_factory: ClassVar[Any] = None

    def _driver(self) -> AgentLoopDriver:
        factory: Callable[[], AgentLoopDriver] | None = type(self).driver_factory
        assert factory is not None, "set driver_factory on the subclass"
        return factory()

    def test_satisfies_structural_protocol(self):
        assert isinstance(self._driver(), AgentLoopDriver)

    def test_capabilities_use_known_vocabulary(self):
        caps = self._driver().capabilities()
        assert isinstance(caps, set)
        assert caps <= CAPABILITY_VOCABULARY, f"unknown capability words: {caps - CAPABILITY_VOCABULARY}"

    def test_agent_loop_is_an_async_generator_function(self):
        assert inspect.isasyncgenfunction(type(self._driver()).agent_loop)

    def test_agent_loop_takes_keyword_only_streaming(self):
        params = inspect.signature(type(self._driver()).agent_loop).parameters
        assert params["streaming"].kind is inspect.Parameter.KEYWORD_ONLY
