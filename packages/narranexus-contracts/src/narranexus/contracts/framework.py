"""
@file_name: framework.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for agent-loop frameworks (slot ``turn.pipeline.act.framework``).

A framework driver runs one agent turn as a stream of raw, provider-agnostic
event dicts. This is the canonical home of the ``AgentLoopDriver`` Protocol
that ``narranexus.platform.agent_framework.loop.driver`` used to define; the
legacy module now re-exports it so every existing import keeps resolving to
the same object.

Contract version: ``API_VERSIONS["framework"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal, Protocol, runtime_checkable

# Every string a driver may declare from ``capabilities()``. Declaring a word
# outside this set is a contract violation caught by the contract test base;
# declaring a word the driver does not honour is a bug the orchestrator cannot
# see (it gates behaviour on the declaration), so declare only what ships.
CAPABILITY_VOCABULARY: frozenset[str] = frozenset(
    {
        "steering",
        "plan",
        "resume",
        "fork",
        "sleep",
        "subagent_announce",
        "event_log",
        "interrupt_soft",
        "raw_context",
        "arg_streaming",
    }
)


@runtime_checkable
class AgentLoopDriver(Protocol):
    """Runs one agent turn as a stream of raw, provider-agnostic events.

    Conforming drivers yield event dicts that the platform's response processor
    knows how to consume. The contract mirrors the original concrete
    implementation (``ClaudeAgentSDK.agent_loop``) — the reference shape every
    framework adapter must match.

    Concurrency: ``agent_loop`` is an async generator; a driver must be safe to
    instantiate per turn and must stop yielding promptly once ``cancellation``
    reports ``requested()``.
    """

    def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        ...

    def capabilities(self) -> set[str]:
        """Feature flags this driver supports beyond the base contract.

        Capability negotiation seam: the orchestrator and frontend switch
        optional behaviour on the declared set instead of hardcoding
        per-framework knowledge. An empty set means "base contract only". The
        remote (HTTP) driver returns ``{"steering"}`` for a steer-capable
        framework (nexus_power) and empty otherwise — it carries steering over
        the hop via the executor's ``/steer`` endpoint + ``steer_consumed`` frames
        (see remote_driver.py / executor_service.py), so its answer is
        framework-aware, not a blanket empty. The consumer is live: the
        orchestrator gates a run's steerability on
        ``"steering" in driver.capabilities()``.

        Every declared string must come from ``CAPABILITY_VOCABULARY`` (declare
        only what actually ships — ``NexusAgent`` ships ``event_log`` and
        ``steering`` today).
        """
        ...


@dataclass(frozen=True)
class InstallComponent:
    """One pip wheel or npm package, version pinned inside ``requirement``.

    ``requirement`` is passed to the package manager verbatim
    (``"claude-agent-sdk==0.1.43"``, ``"@anthropic-ai/claude-code@2.1.220"``);
    installers never re-derive a version.
    """

    kind: Literal["pip", "npm"]
    requirement: str


@dataclass(frozen=True)
class FrameworkInstall:
    """How an on-demand framework is installed and how "installed" is detected.

    ``probe_package`` is the Python import name whose presence means the
    framework's code is there; ``user_version_source`` picks which component's
    detected version the UI shows when there is more than one; ``size_hint``
    is the download-size hint shown before installing.
    """

    components: tuple[InstallComponent, ...]
    probe_package: str
    user_version_source: Literal["npm_cli", "pip_pkg"]
    size_hint: str


@dataclass(frozen=True)
class FrameworkMeta:
    """Static description of a framework, used by the plugin factory UI and loader.

    ``install`` is ``None`` for frameworks that ship inside the host (nexus_power)
    and a ``FrameworkInstall`` for the on-demand ones (claude_code / codex_cli).
    Carried as ``Contribution.meta["framework"]`` so the installer table in the
    backend is derived from the framework registry instead of duplicating it.
    """

    name: str
    display_name: str
    install: FrameworkInstall | None = None


__all__ = [
    "CAPABILITY_VOCABULARY",
    "AgentLoopDriver",
    "InstallComponent",
    "FrameworkInstall",
    "FrameworkMeta",
]
