"""
Agent Framework Package

Provides integration interfaces with different AI Agent SDKs.

Two abstraction axes live here:
  - ``provider_driver/``    — provider axis (which endpoint / key)
  - ``agent_loop_driver``   — framework axis (which agent-loop protocol)

Drivers register themselves under a name; ``step_3_agent_loop`` reads
``user_slots.agent_framework`` and looks up the matching driver via
``get_agent_loop_driver``. Nothing downstream hard-codes a class.

Currently registered drivers:
  - ``"claude"`` / ``"claude_code"``                       → ``ClaudeAgentSDK``
  - ``"codex"`` / ``"codex_cli"`` / ``"codex_cli_v2"`` /
    ``"codex_official"``                                   → ``CodexSDKv2``

**Cutover note (2026-06-08)**: The v1 hand-rolled ``CodexSDK``
implementation lives in ``xyz_codex_cli_sdk.py`` but is no longer
registered. Every ``codex_*`` framework name resolves to the v2
official-SDK driver. The v1 file stays in the repo as a revival
fallback in case the official SDK has a critical regression — pulling
it back online is a single ``register_agent_loop_driver`` line.

The ``codex_cli`` framework also carries a per-call ``codex_config``
ContextVar — see ``api_config.CodexConfig`` for the auth + model
shape the resolver fills before each turn.
"""

from loguru import logger

from .xyz_claude_agent_sdk import ClaudeAgentSDK
from .xyz_codex_cli_sdk import CodexSDK  # noqa: F401 — kept importable; not registered
from .api_config import CodexConfig, codex_config
from .agent_loop_driver import (
    AgentLoopDriver,
    DEFAULT_AGENT_LOOP_FRAMEWORK,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)

# Register the built-in Claude driver under both the legacy
# ``claude`` name (driver registry default) AND the user-facing
# ``claude_code`` name written into ``user_slots.agent_framework``.
register_agent_loop_driver("claude", ClaudeAgentSDK)
register_agent_loop_driver("claude_code", ClaudeAgentSDK)

# Register the official ``openai-codex`` SDK driver under every
# codex framework name (legacy ``codex_cli`` / ``codex`` + the explicit
# ``codex_cli_v2`` / ``codex_official`` names from the A/B period).
# Existing DB rows with ``agent_framework="codex_cli"`` keep working
# transparently — they just now run the v2 implementation underneath.
# The import is guarded so the package still loads on slim deploys
# that exclude ``openai-codex``; callers asking for any codex name on
# such a deploy get a clean ``ValueError`` from
# ``get_agent_loop_driver``.
try:
    from .xyz_codex_official_sdk import CodexSDKv2
    register_agent_loop_driver("codex", CodexSDKv2)
    register_agent_loop_driver("codex_cli", CodexSDKv2)
    register_agent_loop_driver("codex_cli_v2", CodexSDKv2)
    register_agent_loop_driver("codex_official", CodexSDKv2)
except ImportError as _e:  # noqa: BLE001 — guard against any SDK shape
    CodexSDKv2 = None  # type: ignore[assignment]
    logger.warning(
        f"CodexSDKv2 not available ({_e}); ALL codex framework names "
        f"are unregistered until the official ``openai-codex`` SDK is "
        f"installed. Re-install dependencies or enable the v1 fallback "
        f"by registering ``CodexSDK`` against ``codex_cli`` in this file."
    )

__all__ = [
    "ClaudeAgentSDK",
    "CodexSDK",
    "CodexSDKv2",
    "CodexConfig",
    "codex_config",
    "AgentLoopDriver",
    "DEFAULT_AGENT_LOOP_FRAMEWORK",
    "available_agent_loop_frameworks",
    "get_agent_loop_driver",
    "register_agent_loop_driver",
    "resolve_framework_name",
]
