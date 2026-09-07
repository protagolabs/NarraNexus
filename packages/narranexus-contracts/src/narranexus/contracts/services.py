"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-03
@description: Host service contracts named by the ``kernel.*`` slots, plus the ``ServiceRef``s that cross the plugin boundary.

The Protocols are distribution-level choices a plugin never binds at runtime.
Batch 1 fixes their shapes as structural Protocols so every slot in the kernel
tree points at a symbol that exists; the kernel's current implementations
(sqlite/mysql backends, secret_box, local/NetMind auth, the EventBus) become
their default providers when those packages migrate under the kernel.

The ``ServiceRef`` constants at the bottom used to live in
``narranexus/kernel/plugins/service_refs.py``, which meant the kernel — the
one tree where "adding an implementation must not require an edit" is a
charter rule (§2.9) — knew two business plugins by name, and a fourth
cross-plugin service could not be added without editing it. They belong to
the contract package: a ref is a name plus a type, and both sides of the call
(provider and consumer) already depend on contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Mapping, Optional, Protocol, TypeVar, runtime_checkable

from narranexus.contracts._base import PluginError
from narranexus.contracts.job import JobRunOutcome
from narranexus.contracts.skill import SkillWorkspace
from narranexus.contracts.web import WebHost

T = TypeVar("T")


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


# =============================================================================
# Cross-boundary service refs
# =============================================================================


@dataclass(frozen=True)
class ServiceRef(Generic[T]):
    """A typed key for a service on the host locator; equality is by id.

    Lives here, not in the kernel, because a ref is contract data: both sides
    of the call (the plugin that ``expose``s and the one that ``require``s)
    already depend on contracts, and neither should need the kernel to name a
    service. ``narranexus.kernel.plugins.services`` re-exports it for the
    locator implementation, and ``narranexus.sdk`` for plugin authors.
    """

    id: str

    def __post_init__(self) -> None:
        if not self.id or "/" in self.id or " " in self.id:
            raise PluginError(f"service ref id must be a non-empty token, got {self.id!r}")


#: ``(agent_id, user_id) -> SkillWorkspace`` — provided by builtin.skills.
SKILL_WORKSPACES: ServiceRef[Callable[[str, Optional[str]], SkillWorkspace]] = ServiceRef("skills.workspaces")
#: ``(db) -> the job instance service over that db`` — provided by builtin.job.
JOB_INSTANCES: ServiceRef[Callable[[Any], Any]] = ServiceRef("jobs.instances")
#: ``async (agent_id, job_id) -> JobRunOutcome`` — provided by builtin.job.
JOB_RUN_ONCE: ServiceRef[Callable[[str, str], Awaitable[JobRunOutcome]]] = ServiceRef("jobs.run_once")
#: The request-scoped HTTP host API (``contracts.web.WebHost``) — provided by the backend host.
WEB_HOST: ServiceRef[WebHost] = ServiceRef("host.web")

__all__ = [
    "JOB_INSTANCES",
    "JOB_RUN_ONCE",
    "SKILL_WORKSPACES",
    "WEB_HOST",
    "AuthProvider",
    "DatabaseBackend",
    "EventSink",
    "SecretStore",
    "ServiceRef",
]
