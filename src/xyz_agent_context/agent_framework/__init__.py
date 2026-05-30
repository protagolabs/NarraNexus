"""
Agent Framework Package

Provides integration interfaces with different AI Agent SDKs.

Two coding-agent SDK wrappers live here and share the same async-
generator contract — ``agent_loop(messages, mcp_server_urls,
extra_env, cancellation)`` yielding event dicts. Step 3 of the agent
runtime reads ``user_slots.agent_framework`` per user to pick which
wrapper to instantiate.
"""

from .xyz_claude_agent_sdk import ClaudeAgentSDK
from .xyz_codex_cli_sdk import CodexSDK
from .api_config import CodexConfig, codex_config

__all__ = [
    "ClaudeAgentSDK",
    "CodexSDK",
    "CodexConfig",
    "codex_config",
]