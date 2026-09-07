"""
@file_name: sections.py
@author: Bin Liang
@date: 2026-09-07
@description: The five default system-prompt sections, in the order the platform assembled them before the prompt became a slot: security iron rules (cloud only), the user's temporal context, the main narrative, the module instructions and the first-run bootstrap injection. Each is a ``PromptSectionProvider`` contribution; a distribution or ``narranexus.toml`` may reorder or drop them, a plugin may add its own.
"""
from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from narranexus.contracts.prompt import PromptContext
from narranexus.kernel.plugins.registry import Contribution

OWNER = "builtin.prompts"


class SecuritySection:
    """Cloud deployments prepend the iron rules (incident 2026-06-17); local builds do not."""

    id = "security"
    order = 10
    budget_chars = 4000

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if ctx.deployment_mode != "cloud":
            return None
        from narranexus.platform.context_runtime.prompts import SECURITY_IRON_RULES

        return SECURITY_IRON_RULES


class TemporalSection:
    """User temporal context — unless the relocation setting moves it into the turn context."""

    id = "temporal"
    order = 20
    budget_chars = 2000

    async def render(self, ctx: PromptContext) -> Optional[str]:
        from narranexus.platform.settings import settings

        if settings.prompt_turn_context_relocation_enabled or ctx.user_id is None:
            return None
        try:
            return await ctx.runtime._build_user_temporal_block(ctx.user_id)
        except Exception as exc:  # noqa: BLE001 — a missing temporal block never breaks a turn
            logger.warning(f"        Failed to build User Temporal Context: {exc}")
            return None


class NarrativeSection:
    """The main narrative's prompt (its summary + volatile part unless relocated)."""

    id = "narrative"
    order = 30
    budget_chars = 20000

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if not ctx.narrative_list:
            return None
        from narranexus.platform.narrative import NarrativeService
        from narranexus.platform.settings import settings

        main = ctx.narrative_list[0]
        text = await NarrativeService(ctx.agent_id).combine_main_narrative_prompt(
            main, include_volatile=not settings.prompt_turn_context_relocation_enabled
        )
        try:
            ctx.meta["nar_summary_chars"] = len(getattr(main.narrative_info, "current_summary", "") or "")
            ctx.meta["nar_dynamic_entries"] = len(getattr(main, "dynamic_summary", []) or [])
        except Exception:  # noqa: BLE001 — diagnostics must never break a turn
            pass
        return text


class ModulesSection:
    """Every enabled module's instructions (deduped by class, sorted by the runtime)."""

    id = "modules"
    order = 40
    budget_chars = 60000

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if not ctx.module_instructions:
            return None
        return await ctx.runtime._build_module_instructions_prompt(list(ctx.module_instructions))


class BootstrapSection:
    """First-run bootstrap injection (file-read approach) for the owner's first turns; deletes Bootstrap.md once its threshold passed."""

    id = "bootstrap"
    order = 50
    budget_chars = 3000

    async def render(self, ctx: PromptContext) -> Optional[str]:
        try:
            from narranexus.platform.bootstrap.lifecycle import is_bootstrap_active
            from narranexus.platform.context_runtime.prompts import BOOTSTRAP_INJECTION_PROMPT
            from narranexus.platform.repository import AgentRepository

            agent_record = await AgentRepository(ctx.db).get_agent(ctx.agent_id)
            if not (agent_record and agent_record.created_by and agent_record.created_by == ctx.user_id):
                return None
            status = await is_bootstrap_active(ctx.db, ctx.agent_id, agent_record.created_by, agent_record.agent_metadata)
            if status.present and not status.active:
                try:
                    os.remove(status.bootstrap_path)
                    logger.info(
                        f"        Auto-deleted Bootstrap.md after {status.event_count} events "
                        f"(threshold={status.threshold}, agent={ctx.agent_id})"
                    )
                except OSError as rm_err:
                    logger.warning(f"        Failed to auto-delete Bootstrap.md: {rm_err}")
                return None
            if status.active:
                ctx.ctx_data.bootstrap_active = True
                return BOOTSTRAP_INJECTION_PROMPT
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"        Failed to inject Bootstrap: {exc}")
        return None


SECURITY = Contribution("security", lambda: SecuritySection())
TEMPORAL = Contribution("temporal", lambda: TemporalSection())
NARRATIVE = Contribution("narrative", lambda: NarrativeSection())
MODULES = Contribution("modules", lambda: ModulesSection())
BOOTSTRAP = Contribution("bootstrap", lambda: BootstrapSection())
CONTRIBUTIONS = (SECURITY, TEMPORAL, NARRATIVE, MODULES, BOOTSTRAP)

__all__ = ["BOOTSTRAP", "CONTRIBUTIONS", "MODULES", "NARRATIVE", "OWNER", "SECURITY", "TEMPORAL"]
