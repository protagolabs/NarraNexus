"""
@file_name: registry.py
@author: NarraNexus
@date: 2026-08-28
@description: The two installable plugins — Claude Code and Codex CLI —
              as a single lookup table keyed by plugin id (== framework name).

Version pins here:
- the npm CLI's pin is NOT re-typed — it is read from
  ``adapters.claude.cli_binary.PINNED_CLI_VERSION``, the same constant the
  agent loop uses to pick which binary to launch. Bumping that one constant
  keeps both users in sync automatically.
- the two pip pins (``claude-agent-sdk==0.1.43`` / ``openai-codex==0.1.0b3``)
  are EXACT because the installer must request one concrete version, whereas
  pyproject declares ranges (``~=0.1.43`` / ``>=0.1.0b3,<0.2``). They are NOT
  auto-derived: the source of truth for what the CLOUD gets is ``uv.lock``
  (currently 0.1.43 / 0.1.0b3, so the two agree today). Keep this exact-pin in
  step with ``uv.lock`` on any bump — otherwise the cloud base install and a
  local plugin install could land on different versions while both report
  "installed". (A range-parse of pyproject here would only paper over that; the
  real invariant is registry-pin == locked-version.)
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.adapters.claude.cli_binary import PINNED_CLI_VERSION

from .spec import InstallComponent, PluginSpec

PLUGIN_SPECS: dict[str, PluginSpec] = {
    "claude_code": PluginSpec(
        id="claude_code",
        display_name="Claude Code",
        framework_name="claude_code",
        components=(
            InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43"),
            InstallComponent(
                kind="npm",
                requirement=f"@anthropic-ai/claude-code@{PINNED_CLI_VERSION}",
            ),
        ),
        probe_package="claude_agent_sdk",
        user_version_source="npm_cli",
        size_hint="~190 MB",
    ),
    "codex_cli": PluginSpec(
        id="codex_cli",
        display_name="Codex CLI",
        framework_name="codex_cli",
        components=(InstallComponent(kind="pip", requirement="openai-codex==0.1.0b3"),),
        probe_package="openai_codex",
        user_version_source="pip_pkg",
        size_hint="~60 MB",
    ),
}
