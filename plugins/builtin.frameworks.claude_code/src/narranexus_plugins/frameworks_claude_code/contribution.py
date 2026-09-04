"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.frameworks.claude_code — the agent-loop framework contribution (driver factory + install spec).
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta, InstallComponent
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.agent_framework import plugin_paths
from narranexus_plugins.frameworks_claude_code.cli_binary import PINNED_CLI_VERSION


def _factory(**factory_kwargs):
    plugin_paths.activate_pyenv()
    from narranexus_plugins.frameworks_claude_code.sdk import ClaudeAgentSDK

    return ClaudeAgentSDK(**factory_kwargs)


INSTALL = FrameworkInstall(
    components=(
        InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43"),
        InstallComponent(kind="npm", requirement=f"@anthropic-ai/claude-code@{PINNED_CLI_VERSION}"),
    ),
    probe_package="claude_agent_sdk",
    user_version_source="npm_cli",
    size_hint="~190 MB",
)
CONTRIBUTION = Contribution("claude_code", lambda: _factory, meta={"framework": FrameworkMeta("claude_code", "Claude Code", install=INSTALL)})

__all__ = ["CONTRIBUTION", "INSTALL"]
