"""
@file_name: projector.py
@author: Bin Liang
@date: 2026-07-29
@description: ContextProjector implementations.

v1 = PassthroughProjector: the materialized base (platform-built
messages plus the framework's appended harness prompt) followed by this
turn's ledger-projected messages. Compaction is present from v1 but as
a separate concern: the ``CompactionPolicy`` appends replacement
entries, the LEDGER substitutes them in its projection, and this class
just concatenates — upgrading compaction never touches projection.
"""

from __future__ import annotations

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import (
    ProviderMessage,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_loop.contracts.protocols import LedgerView


class PassthroughProjector:
    """Base messages + ledger turn messages (compaction-aware via ledger)."""

    def __init__(self, base_messages: list[ProviderMessage]) -> None:
        # The base is platform property; the projector never rewrites it
        # ("materialization vs self-projection" boundary, made explicit).
        self._base = list(base_messages)

    def project(self, ledger: LedgerView, profile: ProviderProfile) -> list[ProviderMessage]:
        from typing import cast

        provider_messages = getattr(ledger, "provider_messages", None)
        turn_messages = (
            cast(list[ProviderMessage], provider_messages())
            if callable(provider_messages)
            else []
        )
        return [*self._base, *turn_messages]
