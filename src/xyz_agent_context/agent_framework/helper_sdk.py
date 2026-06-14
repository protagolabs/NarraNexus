"""
@file_name: helper_sdk.py
@author: NarraNexus
@date: 2026-06-10
@description: Protocol-agnostic helper_llm SDK factory

Single entry point for every helper_llm call site. Picks the helper
implementation for the CURRENT asyncio task based on which helper
config the resolver installed:

  - anthropic_helper ContextVar set  -> AnthropicHelperSDK (Messages API)
  - otherwise                        -> OpenAIAgentsSDK (Chat Completions)

Call sites never import a concrete SDK class; this keeps the helper
swappable per iron rule #9 (no hard binding to one LLM/protocol) and
lets a single Claude key serve both the agent and helper slots.

Both SDKs expose the same interface (llm_function / llm_stream) and
return the same result-wrapper shapes, so callers are dispatch-blind.
"""

from __future__ import annotations


def get_helper_sdk():
    """Return the helper-LLM SDK instance for the current asyncio task.

    Dispatch reads the per-task ``_anthropic_helper_ctx`` ContextVar
    (set by ``set_user_config`` when the helper_llm slot resolved to an
    anthropic-protocol provider). Imports are lazy to avoid a circular
    import at module load (both SDK modules import api_config).
    """
    from xyz_agent_context.agent_framework.api_config import _anthropic_helper_ctx

    if _anthropic_helper_ctx.get() is not None:
        from xyz_agent_context.agent_framework.anthropic_helper_sdk import (
            AnthropicHelperSDK,
        )
        return AnthropicHelperSDK()
    from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
    return OpenAIAgentsSDK()


__all__ = ["get_helper_sdk"]
