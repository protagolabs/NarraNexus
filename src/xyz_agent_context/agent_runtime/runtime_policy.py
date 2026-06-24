"""
@file_name: runtime_policy.py
@author: NetMind.AI
@date: 2026-06-24
@description: RuntimePolicy — per-run behavioral profile for AgentRuntime.

A RunContext carries one RuntimePolicy. The base AgentRuntime uses OWNER_POLICY
(every restriction off == current owner-facing behavior, zero regression).
The StaticVisitorRuntime subclass carries STATIC_VISITOR_POLICY, the v1 "distrust"
profile for untrusted external IM-channel visitors: skip after-execution hooks
(owner's long-term narrative/memory stays static), run in an ephemeral scratch
workspace, scrub platform credentials and internal identifiers from the agent's
context/env, block writes into the owner's workspace, and use the lightweight
IM short-term memory table instead of the owner's narrative for cross-turn state.

Design rule (铁律): new runtime modes are added as an AgentRuntime SUBCLASS that
takes a RuntimePolicy — never by mutating the main AgentRuntime. Each pipeline
step reads `ctx.policy.<flag>`; the default (OWNER_POLICY) branch is byte-for-byte
the pre-policy behavior.

NOTE (v1 scope): these flags govern DATA/STATE isolation only. They do NOT contain
code-execution (e.g. a visitor's Bash reading files outside the workspace) — that
requires an OS sandbox and is deferred to v2. See the v1 plan
(reference/self_notebook/plans/2026-06-22-im-distrust-v1.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RuntimePolicy:
    """Behavioral profile for a single AgentRuntime turn.

    Every field defaults to the permissive (owner) value, so an unset policy is
    indistinguishable from the historical behavior.

    Attributes:
        skip_after_execution_hooks: Skip Step 5 module after-execution hooks
            (narrative / social / memory writes). Keeps the owner's persistent
            state from being mutated by an untrusted visitor turn.
        scrub_provider_env: Remove platform LLM credentials (ANTHROPIC_API_KEY,
            etc.) from the agent CLI subprocess environment before spawn.
        scrub_internal_ids: Keep internal identifiers (agent_id, owner user_id,
            internal bot/infra IDs) out of the agent's assembled context. Top
            priority for the distrust path — prevents an external user extracting
            the creator's sensitive identifiers via prompt injection.
        workspace_mode: "owner" runs in the agent owner's workspace; "scratch"
            runs in an ephemeral, TTL-bounded per-room scratch directory.
        block_owner_path_writes: Deny Write/Edit tool calls whose target path is
            inside the owner's workspace (best-effort; Bash is NOT covered — that
            needs the v2 sandbox).
        im_short_term: Use the IM short-term memory table (keyed by im_room_id)
            for cross-turn context instead of the owner's narrative.
    """

    skip_after_execution_hooks: bool = False
    scrub_provider_env: bool = False
    scrub_internal_ids: bool = False
    workspace_mode: Literal["owner", "scratch"] = "owner"
    block_owner_path_writes: bool = False
    im_short_term: bool = False


# The owner-facing path. Every restriction off — identical to pre-policy behavior.
OWNER_POLICY = RuntimePolicy()

# The v1 distrust profile for untrusted external IM visitors.
#
# Only the flags ENFORCED in v1 are on — the policy must not claim protections it
# doesn't deliver. The remaining distrust intentions need the v2 sandbox and are
# left OFF here until their enforcement points exist (decided 2026-06-24):
#   - scrub_provider_env: the claude CLI authenticates via ANTHROPIC_API_KEY in its
#     subprocess env, which the agent's Bash inherits; scrubbing it app-side breaks
#     auth. Real fix = v2 credential proxy / sandbox.
#   - scrub_internal_ids: deferred to the v2 sandbox.
#   - block_owner_path_writes: the v1 scratch workspace already keeps the agent's cwd
#     out of the owner's tree; a hard Write/Edit path block is v2.
STATIC_VISITOR_POLICY = RuntimePolicy(
    skip_after_execution_hooks=True,  # v1: step_5 + sync hook_persist_turn
    workspace_mode="scratch",         # v1: ephemeral per-room scratch (step_3)
    im_short_term=True,               # v1: IM short-term table for cross-turn context
    scrub_provider_env=False,         # v2
    scrub_internal_ids=False,         # v2
    block_owner_path_writes=False,    # v2
)
