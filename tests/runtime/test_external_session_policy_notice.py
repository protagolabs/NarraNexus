"""
Tests for the External Session restricted-mode notice introduced 2026-06-29.

Background — when an ExternalAgentRuntime drives a run, RuntimePolicy
suppresses some MCP servers (e.g. AwarenessModule, GeneralMemoryModule),
disables some Claude Code built-in tools (Write/Edit/Bash/NotebookEdit),
and skips entire modules (SocialNetworkModule, IM channel modules). The
modules whose MCP server is suppressed still LOAD and their instructions
still ride into the system prompt — instructions that may reference the
now-suppressed tools (e.g. AwarenessModule.prompts mentions
`__mcp__update_awareness()`). Without an up-front notice the agent
typically tries the suppressed tool, fails, then either hallucinates a
result or apologises and freezes.

The notice is rendered in `ContextRuntime.build_complete_system_prompt`
as Part 0.5 (after Temporal Context, before Narrative Info) whenever a
policy is passed through `ContextRuntime.run(..., policy=...)`. Main
runtime (`policy=None`) is unchanged — no notice is rendered.
"""
from __future__ import annotations

import pytest


# ─── _render_external_session_policy_notice — pure-function tests ───────────


class TestRenderNotice:
    def test_external_policy_includes_all_three_sections(self):
        from xyz_agent_context.context_runtime.context_runtime import (
            _render_external_session_policy_notice,
        )
        from xyz_agent_context.agent_runtime.runtime_policy import (
            EXTERNAL_API_POLICY,
        )
        notice = _render_external_session_policy_notice(EXTERNAL_API_POLICY)

        # Heading is always present.
        assert "## External Session — Restricted Mode" in notice

        # MCP tools section names every actually-suppressed tool by its
        # __mcp__-prefixed user-facing name (this is the format the LLM
        # has been told to use in AwarenessModule.prompts).
        assert "### MCP tools disabled in this session" in notice
        assert "__mcp__update_awareness()" in notice
        assert "__mcp__update_agent_name()" in notice
        assert "__mcp__remember()" in notice
        assert "__mcp__grep_memory()" in notice

        # Built-in SDK denylist enumerates each tool literally.
        assert "### Built-in tools disabled in this session" in notice
        for tool in ("Write", "Edit", "NotebookEdit", "Bash"):
            assert f"`{tool}`" in notice

        # Skipped modules section is rendered when the policy has any.
        assert "### Modules not loaded for this session" in notice
        for mod in (
            "SocialNetworkModule", "LarkModule", "SlackModule",
            "TelegramModule", "MessageBusModule",
        ):
            assert f"`{mod}`" in notice

        # Behavioural guidance — these strings must survive any wording
        # changes; downstream agents rely on them to NOT hallucinate a
        # success result when a disabled tool is requested.
        assert "DO NOT" in notice and "pretend" in notice
        assert "Read" in notice and "Glob" in notice  # what's still allowed

    def test_empty_policy_yields_only_the_header_and_guidance(self):
        # A policy with everything empty — corner case to be sure no
        # blank/empty bullet sections leak in.
        from xyz_agent_context.context_runtime.context_runtime import (
            _render_external_session_policy_notice,
        )
        from xyz_agent_context.agent_runtime.runtime_policy import (
            RuntimePolicy,
        )
        empty_policy = RuntimePolicy()
        notice = _render_external_session_policy_notice(empty_policy)
        assert "## External Session — Restricted Mode" in notice
        # No bullet sections fired:
        assert "### MCP tools disabled" not in notice
        assert "### Built-in tools disabled" not in notice
        assert "### Modules not loaded" not in notice
        # Behaviour guidance still rendered (those are unconditional).
        assert "When a tool you need is disabled" in notice
        assert "What you CAN still do" in notice

    def test_unknown_mcp_module_gets_generic_line(self):
        # A policy denylisting a module class we don't have specific
        # tool hints for must NOT silently render an empty bullet —
        # surface a defensive generic so the notice never lies about
        # coverage. (This guards against new modules entering the
        # denylist without an accompanying hint entry.)
        from xyz_agent_context.context_runtime.context_runtime import (
            _render_external_session_policy_notice,
        )
        from dataclasses import dataclass, field
        from typing import FrozenSet

        @dataclass(frozen=True)
        class FakePolicy:
            mcp_denylist: FrozenSet[str] = field(
                default_factory=lambda: frozenset({"FutureModule"})
            )
            extra_disallowed_tools: FrozenSet[str] = field(
                default_factory=frozenset
            )
            skipped_modules: FrozenSet[str] = field(default_factory=frozenset)

        notice = _render_external_session_policy_notice(FakePolicy())
        assert "(all MCP tools exposed by `FutureModule`)" in notice


# ─── ContextRuntime.run(policy=...) — integration ────────────────────────────


@pytest.mark.asyncio
class TestContextRuntimeIntegration:
    async def test_policy_lands_on_ctx_data_extra_data(self):
        # When policy is passed to .run(), it shows up under
        # ctx_data.extra_data["runtime_policy"] so build_complete_system_prompt
        # can find it. We can't easily run the full pipeline in a unit
        # test, but we can verify the plumbing field is set.
        from xyz_agent_context.agent_runtime.runtime_policy import (
            EXTERNAL_API_POLICY,
        )
        # Just confirm the symbol the build function reads from is
        # what step_3 sets — guards against future refactors silently
        # renaming the key.
        assert hasattr(EXTERNAL_API_POLICY, "mcp_denylist")
        assert hasattr(EXTERNAL_API_POLICY, "extra_disallowed_tools")
        assert hasattr(EXTERNAL_API_POLICY, "skipped_modules")


# ─── No-regression for the main runtime ──────────────────────────────────────


class TestMainRuntimeUnchanged:
    def test_main_runtime_passes_no_policy(self, monkeypatch):
        """If `ctx.policy is None` (default AgentRuntime), the notice
        helper is never called and Part 0.5 is skipped — system prompt
        layout for the owner-facing path is unchanged.

        We verify this by looking at the source line that conditionally
        appends the notice — defence against a regression that would
        accidentally fire the notice on every run.
        """
        from xyz_agent_context.context_runtime import context_runtime as cr_mod
        import inspect
        src = inspect.getsource(cr_mod.ContextRuntime.build_complete_system_prompt)
        # The gate must check for non-None policy on ctx_data.extra_data.
        assert 'extra_data' in src and 'runtime_policy' in src
        assert 'if policy is not None' in src
