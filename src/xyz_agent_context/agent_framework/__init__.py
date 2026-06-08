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
  - ``"claude"`` / ``"claude_code"``         → ``ClaudeAgentSDK``
  - ``"codex"`` / ``"codex_cli"``            → ``CodexSDK`` (v1, hand-rolled)
  - ``"codex_official"`` / ``"codex_cli_v2"`` → ``CodexSDKv2`` (official SDK,
                                              opt-in during the A/B period)

The ``codex_cli`` framework also carries a per-call ``codex_config``
ContextVar — see ``api_config.CodexConfig`` for the auth + model
shape the resolver fills before each turn.
"""

from loguru import logger

from .xyz_claude_agent_sdk import ClaudeAgentSDK
from .xyz_codex_cli_sdk import CodexSDK
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

# Register the v1 Codex CLI driver. ``codex_cli`` is the canonical
# value in ``user_slots.agent_framework``; ``codex`` is a short
# alias for use from env / CLI overrides.
register_agent_loop_driver("codex", CodexSDK)
register_agent_loop_driver("codex_cli", CodexSDK)

# Register the v2 Codex driver (official ``openai-codex`` SDK). The
# import is guarded by try/except: if ``openai-codex`` isn't installed
# (e.g. someone runs a slim deploy that excludes it), the rest of the
# package still imports cleanly and v1 stays available. The framework
# names ``codex_cli_v2`` / ``codex_official`` simply won't resolve —
# callers asking for them get a clean ``ValueError`` from
# ``get_agent_loop_driver`` instead of an import-time crash.
try:
    from .xyz_codex_official_sdk import CodexSDKv2
    register_agent_loop_driver("codex_official", CodexSDKv2)
    register_agent_loop_driver("codex_cli_v2", CodexSDKv2)
except ImportError as _e:  # noqa: BLE001 — guard against any SDK shape
    CodexSDKv2 = None  # type: ignore[assignment]
    logger.warning(
        f"CodexSDKv2 not available ({_e}); the ``codex_cli_v2`` / "
        f"``codex_official`` framework names will be unregistered. "
        f"v1 ``codex_cli`` framework is unaffected."
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
