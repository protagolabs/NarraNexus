"""
XYZ Agent Context - Agent context management system

Provides complete context management for Agent runtime:
- Narrative: Narrative management
- Event: Event management  
- Module: Modular capabilities
- Context: Context building
- Runtime: Runtime coordination
"""

# Single source of truth for the app version: read it from the installed
# package metadata (driven by pyproject [project].version, one of the 5 release
# anchors) instead of hand-maintaining a literal that silently goes stale.
try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("xyz-agent-context")
except Exception:  # noqa: BLE001 — source tree with no install metadata
    __version__ = "0.0.0+unknown"

# Export core components - organized by dependency order
# 1. Schema (data structures, no dependencies)
from .schema import (
    ProgressMessage,
    ProgressStatus,
    AgentTextDelta,
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)

# 2. Utils (utilities, low dependencies)
from .utils import DatabaseClient

# 3. Narrative (narrative and event management)
from .narrative import (
    Narrative,
    Event,
    EventService,
    NarrativeService,
)

# 4. Module (module system)
from .module import (
    XYZBaseModule,
    ModuleService,
    HookManager,
)

# 5. Agent Framework (Agent SDK integration).
# ``ClaudeAgentSDK`` is intentionally NOT eagerly imported here: on the
# lightweight local build ``claude-agent-sdk`` is an optional plugin, so
# importing this top-level package must not require it. It stays importable
# (`from xyz_agent_context import ClaudeAgentSDK`) via the lazy __getattr__
# below, which only pulls the SDK on actual attribute access.

# 6. Context Runtime (context building)
from .context_runtime import ContextRuntime

# 7. Agent Runtime (runtime coordination)
from .agent_runtime import AgentRuntime

__all__ = [
    "__version__",
    "ProgressMessage",
    "ProgressStatus",
    "AgentTextDelta",
    "ModuleConfig",
    "MCPServerConfig",
    "ContextData",
    "DatabaseClient",
    "Narrative",
    "Event",
    "EventService",
    "NarrativeService",
    "XYZBaseModule",
    "ModuleService",
    "HookManager",
    "ClaudeAgentSDK",
    "ContextRuntime",
    "AgentRuntime",
]


def __getattr__(name: str):
    # PEP 562 lazy passthrough: keep ``xyz_agent_context.ClaudeAgentSDK``
    # importable without forcing the optional ``claude-agent-sdk`` plugin at
    # package import. Delegates to agent_framework's own lazy resolver.
    if name == "ClaudeAgentSDK":
        from .agent_framework import ClaudeAgentSDK

        return ClaudeAgentSDK
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
