"""
@file_name: external_agent_runtime.py
@author: NetMind.AI
@date: 2026-06-14
@description: ExternalAgentRuntime — AgentRuntime variant for external
              API sessions, parameterised by a RuntimePolicy object.

External API protocol (v0.4). The main `AgentRuntime` is unchanged; this
subclass plumbs a policy into the same 7-step pipeline by overriding only
the constructor — the rest of the policy enforcement happens downstream
(ModuleService skipping modules, GeneralMemoryModule reading
memory_scope, etc.), driven by `ctx.policy` which `AgentRuntime.run()`
already propagates from `self._policy`.

Future variants (Manyfold mode, replay mode, test mode) follow the same
pattern: subclass `AgentRuntime`, set `self._policy` to a
`RuntimePolicy` instance. Nothing else in this file needs to change for
new modes — they each get their own subclass + policy const.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.runtime_policy import RuntimePolicy


class ExternalAgentRuntime(AgentRuntime):
    """AgentRuntime variant whose behaviour is restricted by a RuntimePolicy.

    The only code-level difference from `AgentRuntime` is `self._policy`
    being a real `RuntimePolicy` instance instead of `None`. Downstream
    consumers (RunContext.policy, ModuleService, policy-aware modules,
    step_3 mcp filtering, agent_loop disallowed_tools) read it via the
    already-existing plumbing.

    Used by `backend/routes/external_api.py` via
    `runtime_factory=lambda: ExternalAgentRuntime(policy=EXTERNAL_API_POLICY)`
    passed into BackgroundRun. Other route layers (chat WS, Lark, Job,
    message_bus) keep instantiating `AgentRuntime` directly — they're
    unaffected.
    """

    def __init__(
        self,
        *args,
        policy: RuntimePolicy,
        **kwargs,
    ):
        """
        Initialize ExternalAgentRuntime.

        Args:
            *args / **kwargs: Forwarded to AgentRuntime.__init__ unchanged.
            policy: Required. The RuntimePolicy instance that drives
                module skipping, MCP suppression, memory scoping, etc.
                Typically `runtime_policy.EXTERNAL_API_POLICY`.
        """
        super().__init__(*args, **kwargs)
        # Override the None default set by AgentRuntime.__init__. From
        # this point on, every `run()` call will set `ctx.policy = policy`
        # and the downstream consumers will apply the restrictions.
        self._policy = policy
        logger.info(
            f"ExternalAgentRuntime initialized with policy "
            f"(skipped_modules={sorted(policy.skipped_modules)}, "
            f"memory_scope={policy.memory_scope}, "
            f"identity_block_mode={policy.identity_block_mode})"
        )


__all__ = ["ExternalAgentRuntime"]


def make_external_runtime_factory(
    policy: Optional[RuntimePolicy] = None,
):
    """Convenience factory builder for `BackgroundRun(runtime_factory=...)`.

    Returns a zero-arg callable that constructs a fresh
    `ExternalAgentRuntime(policy=policy)` on each invocation. If `policy`
    is None, defaults to `EXTERNAL_API_POLICY`.

    Why a factory rather than a pre-built instance: BackgroundRun.drive
    opens the runtime in an `async with` block, which means it owns the
    lifecycle (db client, hook manager) for that run. A shared instance
    across runs would leak state.
    """
    from xyz_agent_context.agent_runtime.runtime_policy import EXTERNAL_API_POLICY

    resolved_policy = policy or EXTERNAL_API_POLICY

    def _factory() -> ExternalAgentRuntime:
        return ExternalAgentRuntime(policy=resolved_policy)

    return _factory
