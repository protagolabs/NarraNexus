"""
@file_name: runtime_policy.py
@author: NetMind.AI
@date: 2026-06-14
@description: RuntimePolicy — the configuration object that drives every
              AgentRuntime variant (External, Manyfold, future modes).

External API protocol (v0.4) — the single source of truth for what an
ExternalAgentRuntime (or any future variant) may and may not do. The main
AgentRuntime DOES NOT consult this object; it is read only by subclasses
and by Modules that have been handed a policy via ModuleService.

Design:
- `frozen=True` so a policy instance can be safely shared across requests
  and aliased into ModuleService without worry about accidental mutation.
- Every field has a default that reproduces today's main-AgentRuntime
  behavior; an empty `RuntimePolicy()` is "no restrictions, owner mode".
- One file = one auditable surface. When reviewing what an external
  session can do, you read this file and the const instance defined here.

The companion subclass `ExternalAgentRuntime` wires the policy into the
existing 7-step pipeline at four points:
  1. Module loading — `skipped_modules` enforced by ModuleService /
     ModuleLoader (the module is never instantiated).
  2. MCP-tool exposure — `mcp_denylist` filters the mcp_urls dict
     handed to the LLM agent_loop (the module still loads + runs its
     in-process hooks; only its MCP URL is hidden from the LLM).
  3. SDK built-in tool exposure — `extra_disallowed_tools` is appended
     to ClaudeAgentSDK's `disallowed_tools` list for that run.
  4. Hook execution — `hook_denylist` filters which hooks the
     HookManager actually invokes per module.

And three policy-aware Modules consult it directly:
  - `GeneralMemoryModule` reads `memory_scope` for SCOPE_USER per-user
    retain/recall (otherwise SCOPE_AGENT, current behavior).
  - `BasicInfoModule` reads `identity_block_mode` for visitor-mode
    rendering in the system prompt.
  - `AwarenessModule` reads `awareness_writable` as a defense-in-depth
    check at the mutation point (even if its MCP tool is suppressed,
    any other code path attempting awareness mutation no-ops).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Literal


@dataclass(frozen=True)
class RuntimePolicy:
    """Policy controlling what an AgentRuntime variant may load and do.

    Used by `ExternalAgentRuntime` (and future variants — Manyfold mode,
    replay mode, test mode). The main `AgentRuntime` does NOT consult
    this; pass-through subclasses do.

    All fields default to "no restriction" so an empty policy reproduces
    main-runtime behavior.
    """

    # ── Module loading ────────────────────────────────────────────────────
    # Module class names (matching `MODULE_MAP` keys) that ModuleService
    # MUST NOT instantiate for this run. Used by ExternalAgentRuntime to
    # suppress SocialNetworkModule and IM channel modules which would
    # otherwise leak cross-session state.
    skipped_modules: FrozenSet[str] = field(default_factory=frozenset)

    # ── MCP-tool exposure (module-level) ──────────────────────────────────
    # Module names (matching `MODULE_MAP` keys) whose MCP server URLs are
    # SUPPRESSED — the module still loads + runs its in-process hooks
    # (data_gathering, after_event_execution, etc.), but the LLM does not
    # see its MCP tools. The classic external-session case is
    # AwarenessModule: keep the awareness CONTENT injected via the data
    # gathering hook, but suppress the `update_awareness` MCP tool so a
    # visitor can't mutate the agent's identity prompt.
    mcp_denylist: FrozenSet[str] = field(default_factory=frozenset)

    # ── SDK built-in tool exposure (per-tool) ─────────────────────────────
    # Tool names appended to ClaudeAgentSDK's `disallowed_tools` list at
    # `agent_loop` invocation. Used to lock down Claude Code's built-in
    # filesystem-write surface for external sessions
    # (Write / Edit / NotebookEdit / Bash). Read / Glob / Grep stay
    # available, so the agent can consult owner-prepared docs.
    extra_disallowed_tools: FrozenSet[str] = field(default_factory=frozenset)

    # ── Hook filtering ────────────────────────────────────────────────────
    # Hook names that MUST NOT be registered or invoked. Hook names are
    # the method names declared on `XYZBaseModule` (e.g.
    # `hook_after_event_execution`); filtering is per-module via
    # `(module_name, hook_name)` tuples encoded as `"<module>.<hook>"`
    # strings for compactness.
    hook_denylist: FrozenSet[str] = field(default_factory=frozenset)

    # ── State mutation gates ──────────────────────────────────────────────
    # When False, any module attempting to MUTATE the agent's persistent
    # awareness (via tool calls or hooks) is short-circuited. The
    # `update_awareness` tool is typically also in tool_denylist; this
    # flag is a defense-in-depth check at the mutation point itself.
    awareness_writable: bool = True

    # ── Memory scoping (v0.4 SCOPE_USER fix) ──────────────────────────────
    # "agent" reproduces today's GeneralMemoryModule behavior: observations
    # are stored at scope_type=SCOPE_AGENT (visible across all sessions on
    # this agent). "user" stores at scope_type=SCOPE_USER, scope_id=user_id
    # so each session sees only its own observations — the per-user
    # isolation required for the External API protocol.
    memory_scope: Literal["agent", "user"] = "agent"

    # ── Prompt identity rendering (v0.4 visitor mode) ─────────────────────
    # "owner" — default. The agent's basic_info module renders identity
    #   normally; for chat trigger, sender vs owner is computed from
    #   `extra_data.sender_user_id` and the owner's display_name.
    # "visitor" — external session mode. basic_info renders an explicit
    #   "you are serving an external visitor (session: X), the agent
    #   owner is Y" framing so the agent doesn't address the visitor as
    #   the owner and doesn't disclose owner-private context.
    # "off" — no identity block at all.
    identity_block_mode: Literal["owner", "visitor", "off"] = "owner"


# =============================================================================
# Pre-built policy constants
# =============================================================================


# The default — empty policy. Reproduces today's main-AgentRuntime
# behavior. Exposed so callers that want to be explicit about "no
# restrictions" can pass this rather than an inline `RuntimePolicy()`.
DEFAULT_POLICY: RuntimePolicy = RuntimePolicy()


# External API session policy. Imported by `external_api.py` route and
# passed via the `runtime_factory` into `BackgroundRun`.
#
# Reasoning for each restriction:
# - SocialNetworkModule + IM channel modules are agent-wide stateful
#   capabilities; loading them in an external session would mean visitor
#   A's interactions appear in entity searches issued in visitor B's
#   session, and IM trigger channels are nonsense for HTTP-driven
#   sessions anyway.
# - Workspace write tools would let a visitor permanently mutate the
#   owner's per-agent workspace. read/list/grep are kept so the agent
#   can still consult owner-prepared materials.
# - `update_awareness` would let a visitor mutate the agent's identity
#   prompt — never allowed.
# - `memory_scope="user"` confines observation retain/recall to the
#   visitor's session_id, eliminating the most acute leak surface.
# - `identity_block_mode="visitor"` rewrites the basic_info identity
#   block so the agent knows it's serving an external visitor and not
#   the owner.
EXTERNAL_API_POLICY: RuntimePolicy = RuntimePolicy(
    skipped_modules=frozenset({
        # Agent-wide entity store. Would let an external visitor's
        # `extract_entity_info` writes show up in entity searches
        # issued by another visitor's session on the same agent.
        "SocialNetworkModule",
        # IM channel modules — external session is HTTP, not IM. Even
        # if the owner has these enabled, an external visitor should
        # not be writing into the owner's Lark / Slack / Telegram bus
        # channels.
        "LarkModule",
        "SlackModule",
        "TelegramModule",
        # Message bus is agent-to-agent; an external visitor is not an
        # agent and shouldn't be able to inject into the bus channel
        # graph.
        "MessageBusModule",
    }),
    mcp_denylist=frozenset({
        # AwarenessModule's MCP server hosts `update_awareness` — a
        # visitor must NOT be able to mutate the agent's identity prompt
        # via prompt injection. Awareness CONTENT still flows in via the
        # data_gathering hook (which runs in-process and is unaffected).
        "AwarenessModule",
        # GeneralMemoryModule's MCP server hosts agent-callable
        # `remember` / `grep_memory`. These tools take agent_id as a
        # parameter and run in a separate process — they have no way to
        # know "which user is calling" so they cannot enforce
        # per-user scope at the query layer. The in-process
        # hook_data_gathering recall IS policy-aware (it filters by
        # SCOPE_USER when memory_scope='user') and still auto-injects
        # relevant memory every turn, so the agent isn't starved of
        # memory; it just can't explicitly cross-search.
        "GeneralMemoryModule",
    }),
    extra_disallowed_tools=frozenset({
        # Claude Code SDK built-in filesystem-write tools. Read / Glob /
        # Grep stay enabled so the agent can consult owner-prepared docs
        # but cannot pollute the workspace from an external session.
        "Write",
        "Edit",
        "NotebookEdit",
        # Bash can write via redirects / cp / mv / rm — disallow it too.
        # An external customer-service agent doesn't need shell access.
        "Bash",
    }),
    hook_denylist=frozenset({
        # Populated as Phase 1.3 audit identifies specific cross-session
        # hooks. Leave empty until a concrete leak is named.
    }),
    awareness_writable=False,
    memory_scope="user",
    identity_block_mode="visitor",
)


__all__ = ["RuntimePolicy", "DEFAULT_POLICY", "EXTERNAL_API_POLICY"]
