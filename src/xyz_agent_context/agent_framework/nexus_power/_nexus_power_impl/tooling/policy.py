"""
@file_name: policy.py
@author: Bin Liang
@date: 2026-07-29
@description: PolicyEngine — an ordered layer list where deny always
wins and a layer's internal exception counts as deny.

Fail-closed is a deliberate home-grown decision (Codex fails open;
OpenClaw's empty allowlist admits) — on a multi-tenant cloud, security
errs toward "off". v1 layers: the disallowed-tools contract (A1) and
workspace confinement. Future layers (platform deny set, repetition
observation, per-agent policy) append; subagent inheritance is passing
the engine INSTANCE to the subagent channel.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ALLOW,
    Decision,
    PolicyContext,
    PolicyVerdict,
    ToolCall,
)

# Builtin argument names that carry filesystem paths (the confinement
# layer's inspection surface).
_PATH_ARG_NAMES = ("path", "file_path", "directory")


class PolicyEngine:
    """Runs every layer in order; any deny (or layer crash) denies."""

    def __init__(self, layers: tuple) -> None:
        self._layers = tuple(layers)

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        for layer in self._layers:
            try:
                decision = layer.check(call, ctx)
            except Exception as exc:  # noqa: BLE001 - fail-closed by design
                logger.warning(
                    f"policy layer {type(layer).__name__} crashed on "
                    f"{call.name}: {exc} — denying"
                )
                return Decision(
                    PolicyVerdict.DENY,
                    f"policy layer {type(layer).__name__} failed (fail-closed)",
                )
            if not decision.allowed:
                return decision
        return ALLOW


class DisallowedToolsLayer:
    """Contract A1: the driver-supplied disallowed set MUST take effect."""

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        if call.name in ctx.disallowed_tools:
            return Decision(
                PolicyVerdict.DENY, f"tool '{call.name}' is disallowed for this turn"
            )
        return ALLOW


class WorkspaceConfinementLayer:
    """Path arguments of builtin tools must resolve inside the workspace.

    No silent rewriting: an escape attempt is denied with the resolved
    path named, so the model can correct itself. MCP tools are not
    path-inspected here (their side effects live server-side).
    """

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        if call.name.startswith("mcp__"):
            return ALLOW
        workspace = Path(ctx.tool_ctx.workspace).resolve()
        for arg_name in _PATH_ARG_NAMES:
            raw = call.args.get(arg_name)
            if not isinstance(raw, str) or not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            resolved = candidate.resolve()
            if not resolved.is_relative_to(workspace):
                return Decision(
                    PolicyVerdict.DENY,
                    f"path '{raw}' resolves outside the workspace ({resolved})",
                )
        return ALLOW
