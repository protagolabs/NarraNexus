"""
@file_name: static_visitor_runtime.py
@author: NetMind.AI
@date: 2026-06-24
@description: StaticVisitorRuntime — AgentRuntime variant for untrusted external
IM-channel visitors (v1 distrust path).

It is a thin subclass: it inherits the entire run() pipeline unchanged and only
swaps the per-run RuntimePolicy. Every behavioral difference (skip after-execution
hooks, scratch workspace, env/identifier scrub, owner-path write block, IM
short-term memory) is expressed declaratively via the policy and read by the
individual pipeline steps as `ctx.policy.<flag>`. The main AgentRuntime is never
modified — the owner-facing path keeps OWNER_POLICY and is byte-for-byte unchanged.

Routing: the channel trigger selects this runtime (instead of AgentRuntime) when a
binding is untrusted and the sender is not the owner. See the v1 plan
(reference/self_notebook/plans/2026-06-22-im-distrust-v1.md).
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.runtime_policy import (
    RuntimePolicy,
    STATIC_VISITOR_POLICY,
)

if TYPE_CHECKING:
    from xyz_agent_context.agent_runtime.run_collector import RunCollection


class StaticVisitorRuntime(AgentRuntime):
    """AgentRuntime that runs a turn under a distrust RuntimePolicy.

    Args:
        policy: The behavioral profile to run under. Defaults to
            STATIC_VISITOR_POLICY (the v1 distrust profile). Accepting it as a
            parameter keeps the subclass reusable for future, stricter profiles
            without further subclassing.
        **kwargs: Forwarded to AgentRuntime (database_client, response_processor,
            hook_manager, use_async_db).
    """

    def __init__(self, policy: Optional[RuntimePolicy] = None, **kwargs):
        super().__init__(**kwargs)
        self._policy = policy if policy is not None else STATIC_VISITOR_POLICY

    async def run_and_collect(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any,
        **extra_kwargs: Any,
    ) -> "RunCollection":
        """Drive one distrust run to completion and return its grouped output.

        The channel trigger calls this directly for a distrust turn — instead of
        ``get_agent_runtime_client().run_and_collect()`` (which always constructs a
        plain AgentRuntime). Mirrors ``InProcessAgentRuntimeClient.run_and_collect``,
        but drives ``self`` so the distrust RuntimePolicy is carried through the run.
        Imports are local to avoid the channel/__init__ ↔ AgentRuntime circular
        import (same reason as the client).
        """
        from xyz_agent_context.agent_runtime.admission import (
            get_admission_controller,
        )
        from xyz_agent_context.agent_runtime.run_collector import collect_run

        # Two-level concurrency gate (no-op locally; enforced in cloud). Keyed on
        # user_id == owner, so a distrust turn counts against the owner's budget —
        # the owner pays and owns the resource (binding rule #14: delays start,
        # never interrupts).
        async with get_admission_controller().slot(user_id):
            return await collect_run(
                self,
                agent_id=agent_id,
                user_id=user_id,
                input_content=input_content,
                working_source=working_source,
                **extra_kwargs,
            )
