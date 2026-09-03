"""
@file_name: driver.py
@author: Bin Liang
@date: 2026-05-29
@description: Pluggable agent-loop framework abstraction.

The 7-step pipeline's step_3 runs one agent turn. Historically it
hard-instantiated ``ClaudeAgentSDK``, binding the whole platform to a
single agent framework — exactly the "one switch away from breaking"
risk iron rule #9 forbids. This module introduces a thin Protocol +
registry so a new framework (OpenAI Agents SDK as a full loop,
LangGraph, a home-grown loop, …) is added by REGISTERING a driver,
never by editing step_3.

Two orthogonal abstraction axes already exist in this package:
  - provider axis  -> ``provider_driver/`` (which endpoint / key)
  - framework axis -> THIS module          (which agent-loop protocol)
They compose: a framework driver still resolves its model/endpoint
through the provider layer.

Selection precedence (most specific wins):
  1. explicit ``framework`` arg to ``get_agent_loop_driver()``
     (the per-agent extension point — pass an agent-scoped choice here)
  2. env var ``AGENT_LOOP_FRAMEWORK``
  3. ``DEFAULT_AGENT_LOOP_FRAMEWORK`` ("nexus_power")
"""

from __future__ import annotations

import os
from typing import Any, Callable

from loguru import logger

# The Protocol itself is the public contract and lives in narranexus.contracts
# (plugin platform, batch 0). It is re-exported here so every existing import
# of ``AgentLoopDriver`` from this module keeps resolving to the same object.
from narranexus.contracts import Disposable, UnknownEntry
from narranexus.contracts.framework import AgentLoopDriver
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Registry


class FrameworkNotInstalledError(RuntimeError):
    """A registered framework's optional plugin is not installed.

    Distinct from the ``ValueError`` raised for an UNKNOWN framework: the name
    is valid and known, but on the lightweight local build its SDK plugin
    (``claude-agent-sdk`` / ``openai-codex``) has not been installed yet. This
    is a fail-closed stop — the run refuses with an actionable message rather
    than silently falling back to another framework (which would run the user's
    agent on a framework they did not choose).

    Where this surfaces: config time is the PRIMARY guard — the selector greys
    out an uninstalled framework and ``POST /agent-framework`` returns 409, so a
    user cannot normally bind one. This exception is the runtime BACKSTOP for a
    pre-existing binding (a desktop user who upgrades with an agent already set
    to claude_code, or bound-then-uninstalled): it propagates out of the agent
    turn through the normal run-error surface carrying the English message
    below. There is NO dedicated route catch and no per-framework localisation
    yet — a caller that wants a localised, per-framework hint should catch this
    and read ``exc.framework``. Keep this docstring honest about that.
    """

    def __init__(self, framework: str) -> None:
        self.framework = framework
        super().__init__(
            f"Framework '{framework}' is not installed. Install it from "
            f"Settings → Plugins before running."
        )


DriverFactory = Callable[..., AgentLoopDriver]

DEFAULT_AGENT_LOOP_FRAMEWORK = "nexus_power"

# The kernel registry for slot ``turn.act.framework`` (plugin platform, batch 0).
# Keys are case-insensitive; entries are lazy factories so registering a
# framework never imports its SDK.
FRAMEWORK_REGISTRY: Registry[DriverFactory] = KERNEL_REGISTRIES.registry_for("turn.act.framework")


def register_agent_loop_driver(
    name: str, factory: DriverFactory, *, owner: str = "builtin.frameworks"
) -> Disposable:
    """Register a framework driver factory under a case-insensitive name.

    The factory is called with whatever keyword args ``get_agent_loop_driver``
    forwards (currently ``working_path``); it must return an
    ``AgentLoopDriver``. Re-registering a name overrides it (useful for
    tests injecting a fake driver); the returned ``Disposable`` unregisters it
    again, which is how a test cleans up.
    """
    key = name.strip().lower()
    if key in FRAMEWORK_REGISTRY:
        logger.debug(f"Overriding agent-loop driver '{key}'")
    return FRAMEWORK_REGISTRY.register(key, lambda: factory, owner=owner, replace=True)


def available_agent_loop_frameworks() -> list[str]:
    """Names of all registered frameworks (sorted, for stable logging)."""
    return sorted(FRAMEWORK_REGISTRY.names())


def resolve_framework_name(framework: str | None = None) -> str:
    """Apply the selection precedence and return the resolved name."""
    return (
        framework
        or os.getenv("AGENT_LOOP_FRAMEWORK")
        or DEFAULT_AGENT_LOOP_FRAMEWORK
    ).strip().lower()


def get_agent_loop_driver(
    framework: str | None = None,
    *,
    executor_url: str | None = None,
    **factory_kwargs: Any,
) -> AgentLoopDriver:
    """Resolve and construct the agent-loop driver for this turn.

    Args:
        framework: explicit framework name; ``None`` falls through to env
            / default. This is the per-agent extension point.
        executor_url: explicit per-user Executor URL (resolved via the
            broker). Overrides the static ``AGENT_EXECUTOR_URL`` env. When
            ``None``/empty, falls back to the env var (local → unset →
            in-process driver).
        **factory_kwargs: forwarded verbatim to the driver factory
            (e.g. ``working_path``).

    Raises:
        FrameworkNotInstalledError: a known plugin framework (claude_code /
            codex_cli) whose optional SDK is not installed on the local build
            (the fail-closed backstop — see the class docstring).
        ValueError: the resolved framework name is not registered — fail
            loud rather than silently fall back, so a typo in config is
            caught immediately instead of masquerading as "claude".
    """
    name = resolve_framework_name(framework)

    # Executor seam (binding rule #7/#9/#20): route the loop to a remote
    # Executor when an executor URL is available — per-user (resolved by
    # the broker, passed as `executor_url`) or the static env fallback
    # (`AGENT_EXECUTOR_URL`). So claude/codex only ever spawn in that one
    # isolated container. No URL (local / desktop, or inside the executor
    # container itself) → in-process driver below, behaviour unchanged.
    resolved_executor_url = (executor_url or os.getenv("AGENT_EXECUTOR_URL", "")).strip()
    if resolved_executor_url:
        from xyz_agent_context.agent_framework.loop.remote_driver import (
            RemoteAgentLoopDriver,
        )
        return RemoteAgentLoopDriver(
            framework=name, executor_url=resolved_executor_url, **factory_kwargs
        )

    try:
        factory = FRAMEWORK_REGISTRY.get(name)
    except UnknownEntry:
        raise ValueError(
            f"Unknown agent-loop framework '{name}'. "
            f"Registered: {available_agent_loop_frameworks() or '[]'}. "
            f"Register one via register_agent_loop_driver()."
        ) from None

    # Fail-closed on the lightweight local build: a PLUGIN framework
    # (claude_code / codex_cli) whose optional SDK is not installed must refuse
    # here, BEFORE building the driver (whose lazy SDK import would otherwise
    # throw a raw ImportError mid-turn). The gate is scoped to plugin
    # frameworks only — a built-in (nexus_power) or any custom-registered
    # driver is available by virtue of being registered. Only the in-process
    # path reaches this: the remote-executor branch above returned already, and
    # cloud executors pre-install every SDK so the check passes there. Imported
    # locally to avoid an import cycle with this package's __init__.
    from xyz_agent_context.agent_framework import plugin_paths

    if name in plugin_paths.PLUGIN_FRAMEWORKS and not plugin_paths.framework_installed(name):
        raise FrameworkNotInstalledError(name)

    return factory(**factory_kwargs)
