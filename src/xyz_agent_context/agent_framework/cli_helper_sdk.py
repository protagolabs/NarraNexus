"""
@file_name: cli_helper_sdk.py
@author:
@date: 2026-07-07
@description: CLI-backed helper_llm caller (subscription / OAuth helper).

When the helper_llm slot points at a subscription provider — Claude Code
(``claude_oauth``) or Codex (``codex_oauth``) — the OAuth credential cannot
make direct Messages / Chat-Completions API calls, so the helper's small
structured-output calls run through the SAME CLI the subscription authorizes.
This is what lets a single subscription login cover BOTH the agent slot and
the helper_llm slot with no separate API key.

Interface-compatible with OpenAIAgentsSDK / AnthropicHelperSDK
(``llm_function`` / ``llm_stream``) so the ~15 helper call sites work unchanged
through ``get_helper_sdk()``. Call sites never import this class directly.

Two backends, chosen by ``cli_helper_config.framework``:
  - "claude_code": one-shot ``claude_agent_sdk.query()`` (tool-free, single
    turn), reusing the same ``ClaudeConfig.to_cli_env`` credential wiring the
    agent loop uses.
  - "codex_cli": the registered codex agent-loop driver in a one-shot,
    reusing the ambient ``codex_config`` (already set to the subscription for
    a codex-agent user) and its tested CODEX_HOME / credential staging.

Structured output uses the same prompt-engineered path as AnthropicHelperSDK
(schema embedded in the system prompt, JSON extracted + validated
client-side), reusing its extractor and result wrappers so downstream
consumers see identical shapes.
"""

from __future__ import annotations

import os
import tempfile
from typing import AsyncGenerator, Optional, Type

from loguru import logger
from pydantic import BaseModel, TypeAdapter

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    cli_helper_config,
)
from xyz_agent_context.agent_framework.openai_agents_sdk import (
    _ParsedResult,
    _SimpleResult,
    _extract_json_from_llm_output,
    _last_llm_call_info,
)
from xyz_agent_context.utils.cost_tracker import (
    get_cost_context,
    record_cost,
    warn_missing_usage,
)
from xyz_agent_context.utils.logging import timed

# Cheap sensible defaults per framework when the slot model is empty/"default".
_DEFAULT_CLAUDE_HELPER_MODEL = "haiku"
_DEFAULT_CODEX_HELPER_MODEL = "gpt-5.1-codex-mini"

# Reusable neutral cwd for the tool-free claude one-shot — the CLI requires a
# working directory to exist but the helper never touches the filesystem
# (allowed_tools=[]). One shared dir avoids per-call mkdtemp churn.
_HELPER_CWD = os.path.join(tempfile.gettempdir(), "narranexus-cli-helper")


class CliHelperSDK:
    """Helper-LLM client that runs one-shot completions through a coding CLI."""

    def __init__(self):
        pass

    @staticmethod
    def _resolve_model(requested_model: Optional[str]) -> str:
        """Slot model wins; per-call-site ``model=`` overrides are ignored.

        Call sites pass OpenAI-flavoured model names (e.g. the narrative
        judge's gpt-5.4-mini) that don't exist on a Claude/Codex subscription.
        Fall back to the framework's cheap default when the slot model is
        empty or the "default" sentinel.
        """
        slot_model = cli_helper_config.model
        if slot_model and slot_model != "default":
            return slot_model
        if cli_helper_config.framework == "codex_cli":
            return _DEFAULT_CODEX_HELPER_MODEL
        return _DEFAULT_CLAUDE_HELPER_MODEL

    @staticmethod
    def _build_system_prompt(instructions: str, output_type: Optional[Type[BaseModel]]) -> str:
        if not output_type:
            return instructions
        schema_obj = output_type.model_json_schema()
        import json as _json
        return instructions + (
            "\n\nYou MUST respond with ONLY a valid JSON object matching "
            "this schema. No markdown, no code blocks, no explanation, "
            "no <think> tags. ONLY the raw JSON object.\n"
            f"Schema: {_json.dumps(schema_obj, ensure_ascii=False)}"
        )

    async def _run_claude_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, int, int]:
        """One-shot, tool-free ``claude_agent_sdk.query()``.

        Reuses ClaudeConfig.to_cli_env so an OAuth subscription's blank
        api_key makes the CLI read ~/.claude credentials, exactly like the
        agent loop. Returns (text, input_tokens, output_tokens).
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        from xyz_agent_context.agent_framework.model_catalog import resolve_cli_alias

        cfg = ClaudeConfig(
            api_key=cli_helper_config.api_key,
            base_url=cli_helper_config.base_url,
            model=model_name,
            auth_type=cli_helper_config.auth_type,
        )
        os.makedirs(_HELPER_CWD, exist_ok=True)
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=resolve_cli_alias(model_name, auth_type=cli_helper_config.auth_type),
            env=cfg.to_cli_env(),
            allowed_tools=[],      # pure completion — no tool use
            mcp_servers={},
            max_turns=1,
            cwd=_HELPER_CWD,
        )

        text_parts: list[str] = []
        result_text = ""
        in_tok = out_tok = 0
        async for msg in query(prompt=user_input, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                result_text = msg.result or ""
                raw_usage = getattr(msg, "usage", None)
                if isinstance(raw_usage, dict):
                    in_tok = int(raw_usage.get("input_tokens", 0) or 0)
                    out_tok = int(raw_usage.get("output_tokens", 0) or 0)
        text = "".join(text_parts) or result_text
        return text, in_tok, out_tok

    async def _run_codex_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, int, int]:
        """One-shot via the registered codex agent-loop driver.

        Reuses the ambient ``codex_config`` (already the subscription for a
        codex-agent user) and the driver's tested CODEX_HOME / credential
        staging. We prepend the schema/instructions to the user turn and
        accumulate the streamed text deltas. Best-effort: codex is an agentic
        CLI, so JSON reliability rests on the schema prompt + extractor. Usage
        is read from the terminal event when the driver reports it.
        """
        from xyz_agent_context.agent_framework import get_agent_loop_driver

        driver = get_agent_loop_driver(framework="codex_cli")
        prompt = f"{system_prompt}\n\n---\n\n{user_input}"
        text_parts: list[str] = []
        in_tok = out_tok = 0
        async for ev in driver.agent_loop(
            messages=[{"role": "user", "content": prompt}],
            mcp_server_urls={},
        ):
            if not isinstance(ev, dict):
                continue
            raw = ev.get("raw_event") if ev.get("type") == "raw_response_event" else None
            if raw and raw.get("type") == "response.text.delta":
                delta = raw.get("delta") or ""
                if delta:
                    text_parts.append(delta)
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
            if usage:
                in_tok = int(usage.get("input_tokens", in_tok) or in_tok)
                out_tok = int(usage.get("output_tokens", out_tok) or out_tok)
        return "".join(text_parts), in_tok, out_tok

    async def _run_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, int, int]:
        if cli_helper_config.framework == "codex_cli":
            return await self._run_codex_oneshot(system_prompt, user_input, model_name)
        return await self._run_claude_oneshot(system_prompt, user_input, model_name)

    @timed("llm.cli_helper.llm_function", slow_threshold_ms=15000)
    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: Type[BaseModel] = None,
        model: str = None,
        agent_id: Optional[str] = None,
        db=None,
        reasoning_effort: Optional[str] = None,
    ):
        """Run a one-shot helper completion through the subscription's CLI.

        ``reasoning_effort`` is accepted for interface parity and ignored (the
        one-shot CLI path has no per-call knob; the platform never errors on a
        user's parameter choice — iron rule #15).
        """
        model_name = self._resolve_model(model)
        framework = cli_helper_config.framework
        system_prompt = self._build_system_prompt(instructions, output_type)
        logger.debug(
            f"[CliHelper] one-shot: framework={framework} model={model_name} "
            f"output_type={output_type.__name__ if output_type else 'None'}"
        )

        raw_content, input_tokens, output_tokens = await self._run_oneshot(
            system_prompt, user_input, model_name
        )

        # Cost accounting — same hooks as the other helpers. OAuth subscription
        # calls may report zero tokens (the CLI bills the subscription, not us);
        # record when present, warn (not error) when absent.
        _agent_id, _db = self._resolve_cost_context(agent_id, db)
        if _agent_id and _db:
            if input_tokens > 0 or output_tokens > 0:
                try:
                    await record_cost(
                        db=_db, agent_id=_agent_id, event_id=None,
                        call_type="llm_function", model=model_name,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )
                except Exception as e:
                    logger.warning(f"[CliHelper] failed to record cost: {e}")
            else:
                warn_missing_usage("CliHelper", model_name, "llm_function")

        if not output_type:
            _last_llm_call_info.set({"model": model_name, "structured": "cli_no_schema"})
            return _SimpleResult(raw_content, None)

        json_str = _extract_json_from_llm_output(raw_content)
        if json_str is None:
            logger.warning(
                f"[CliHelper] could not extract JSON from {framework}/"
                f"{model_name}: head={raw_content[:200]!r}"
            )
            raise ValueError(
                f"Could not extract JSON from CLI helper response "
                f"(framework={framework}, model={model_name}): {raw_content[:200]}"
            )

        adapter = TypeAdapter(output_type)
        try:
            parsed = adapter.validate_json(json_str)
        except Exception as e:
            logger.warning(
                f"[CliHelper] schema validation failed on {framework}/"
                f"{model_name}: {e!r} json={json_str[:200]!r}"
            )
            raise
        _last_llm_call_info.set({"model": model_name, "structured": "cli_prompt"})
        return _ParsedResult(parsed, raw_content, None)

    @timed("llm.cli_helper.llm_stream", slow_threshold_ms=15000)
    async def llm_stream(
        self,
        instructions: str,
        user_input: str,
        model: str = None,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a plain-text helper reply.

        The one-shot CLI path has no incremental stream we can forward here,
        so we run the completion and yield the full text once. Matches the
        OpenAI/Anthropic helper interface for the fallback-reply path.
        """
        model_name = self._resolve_model(model)
        _last_llm_call_info.set({"model": model_name, "structured": "cli_stream"})
        raw_content, input_tokens, output_tokens = await self._run_oneshot(
            instructions, user_input, model_name
        )
        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db and (input_tokens > 0 or output_tokens > 0):
            try:
                await record_cost(
                    db=_db, agent_id=_agent_id, event_id=None,
                    call_type="llm_stream", model=model_name,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                )
            except Exception as e:
                logger.warning(f"[CliHelper-Stream] failed to record cost: {e}")
        if raw_content:
            yield raw_content

    def _resolve_cost_context(self, agent_id, db):
        _agent_id, _db = agent_id, db
        if not _agent_id or not _db:
            ctx = get_cost_context()
            if ctx:
                _agent_id, _db = ctx
        return _agent_id, _db
