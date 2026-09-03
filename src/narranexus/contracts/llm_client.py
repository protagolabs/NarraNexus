"""
@file_name: llm_client.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for helper-LLM clients (slot ``model.clients``).

The helper LLM is the platform's "atomic call" axis: one instructions +
user_input request, optionally with a structured output type, or a plain
text stream. Three clients exist today (anthropic / openai / cli) behind one
protocol key; a plugin adds a fourth by registering a client under a new key.
Call sites are dispatch-blind — they never import a concrete client.

Contract version: ``API_VERSIONS["llm_client"]``.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable


@runtime_checkable
class LlmClient(Protocol):
    """Structural contract for a helper-LLM client instance."""

    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: Any = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        db: Any = None,
        reasoning_effort: Optional[str] = None,
    ) -> Any:
        """One request → one result (structured when ``output_type`` is given).

        ``model`` is a call-site preference; the client resolves the effective
        model against the slot configuration and may ignore the preference.
        """
        ...

    def llm_stream(
        self,
        instructions: str,
        user_input: str,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """One request → plain-text deltas."""
        ...


__all__ = ["LlmClient"]
