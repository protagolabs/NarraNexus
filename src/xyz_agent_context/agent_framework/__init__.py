"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Agent Framework — the standalone framework layer. Module map:

- loop/       Agent-loop execution (driver abstraction, remote executor
              delegation, broker client, output transfer, circuit breaker)
- adapters/   Agent-framework adapters (claude/, codex/, openai_agents;
              the swap seam demanded by binding rule #9)
- llm/        Atomic LLM operations — single calls, no loop (helper SDK
              family, failure classification, embeddings api, transcription/)
- providers/  Provider & model catalog system (registry/resolver/readiness,
              system/user/slot services, model catalog+sync+probe, driver/)
- plugin_paths.py  WHERE optional framework plugins install + are they present
- api_config.py    Cross-cutting per-adapter config dataclasses (root)

This __init__ is also the driver REGISTRATION point (claude_code / codex_cli /
nexus_power) and the stable public symbol surface.

Lightweight-plugin build
------------------------
On the local build ``claude-agent-sdk`` (Claude) and ``openai-codex`` (Codex)
are OPTIONAL plugins the user installs on demand — importing this package must
NOT require either. So all three frameworks register LAZY factories: the SDK
import happens inside the factory (and inside ``__getattr__`` for the public
class names), never at package import. Each factory first calls
``plugin_paths.activate_pyenv()`` so a plugin installed while the app runs
resolves without a restart. ``framework_installed`` (in ``plugin_paths``) is
the separate "is it actually present" gate; registration only means "knows how
to build it once installed". The public names ``ClaudeAgentSDK`` / ``CodexSDK``
/ ``CodexSDKv2`` stay importable via module ``__getattr__`` (PEP 562), still
lazily.
"""

from . import plugin_paths
from .api_config import CodexConfig, codex_config
from .loop.driver import (
    AgentLoopDriver,
    DEFAULT_AGENT_LOOP_FRAMEWORK,
    FrameworkNotInstalledError,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)


# --- Lazy driver factories -------------------------------------------------
# Each imports its SDK only when actually building a driver, after putting the
# plugin pyenv on sys.path. None of these run at package import time.

def _nexus_power_factory(**factory_kwargs):
    # Home-grown loop — no external dependency, always available.
    from .adapters.nexus.nexus_agent import NexusAgent

    return NexusAgent(**factory_kwargs)


def _claude_code_factory(**factory_kwargs):
    plugin_paths.activate_pyenv()
    from .adapters.claude.sdk import ClaudeAgentSDK

    return ClaudeAgentSDK(**factory_kwargs)


def _codex_cli_factory(**factory_kwargs):
    plugin_paths.activate_pyenv()
    from .adapters.codex.official_sdk import CodexSDKv2

    return CodexSDKv2(**factory_kwargs)


register_agent_loop_driver("nexus_power", _nexus_power_factory)
register_agent_loop_driver("claude_code", _claude_code_factory)
register_agent_loop_driver("codex_cli", _codex_cli_factory)

# Cover plugins already installed at process start: append the pyenv once so
# the lazy imports above (and the helper-LLM paths in llm/cli_helper.py,
# providers/driver/drivers/claude_oauth.py) resolve. Idempotent; a no-op when
# nothing is installed. The per-factory call handles install-during-runtime.
plugin_paths.activate_pyenv()


# --- Lazy public class names (PEP 562) -------------------------------------
# Kept importable (`from ...agent_framework import ClaudeAgentSDK`) without
# forcing the SDK at package import. Only materializes on actual attribute
# access.

def __getattr__(name: str):
    if name == "ClaudeAgentSDK":
        plugin_paths.activate_pyenv()
        from .adapters.claude.sdk import ClaudeAgentSDK

        return ClaudeAgentSDK
    if name == "CodexSDK":
        plugin_paths.activate_pyenv()
        from .adapters.codex.cli_sdk import CodexSDK

        return CodexSDK
    if name == "CodexSDKv2":
        plugin_paths.activate_pyenv()
        from .adapters.codex.official_sdk import CodexSDKv2

        return CodexSDKv2
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClaudeAgentSDK",
    "CodexSDK",
    "CodexSDKv2",
    "CodexConfig",
    "codex_config",
    "AgentLoopDriver",
    "DEFAULT_AGENT_LOOP_FRAMEWORK",
    "FrameworkNotInstalledError",
    "available_agent_loop_frameworks",
    "get_agent_loop_driver",
    "register_agent_loop_driver",
    "resolve_framework_name",
    "plugin_paths",
]
