"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-03
@description: Host service contracts named by the ``kernel.*`` slots (database, secrets, auth, event sink).

These are distribution-level choices a plugin never binds at runtime. Batch 1
fixes their shapes as structural Protocols so every slot in the kernel tree
points at a symbol that exists; the kernel's current implementations
(sqlite/mysql backends, secret_box, local/NetMind auth, the EventBus) become
their default providers when those packages migrate under the kernel.
"""
from __future__ import annotations

from typing import Any, Awaitable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class DatabaseBackend(Protocol):
    """Slot ``kernel.db``: the dialect-specific database driver."""

    dialect: str

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any: ...

    async def probe(self) -> bool: ...


@runtime_checkable
class SecretStore(Protocol):
    """Slot ``kernel.secrets``: secrets encrypted at rest, keyed by owner + name."""

    def get(self, owner: str, name: str) -> str | None: ...

    def put(self, owner: str, name: str, value: str) -> None: ...

    def delete(self, owner: str, name: str) -> None: ...


@runtime_checkable
class AuthProvider(Protocol):
    """Slot ``kernel.auth`` (distribution-only): resolves a request to an identity or ``None``."""

    def authenticate(self, request: Any) -> Awaitable[Mapping[str, Any] | None]: ...


@runtime_checkable
class EventSink(Protocol):
    """Slot ``kernel.events``: where host events are emitted."""

    async def emit(self, name: str, payload: Mapping[str, Any]) -> Any: ...


__all__ = ["AuthProvider", "DatabaseBackend", "EventSink", "SecretStore"]
