"""
@file_name: greeting_seed.py
@author: Bin Liang
@date: 2026-08-20
@description: The "should this agent's bootstrap greeting be seeded?" decision.

Background — the bootstrap greeting reached DB history only lazily, when
ChatModule.hook_persist_turn ran (it prepends the greeting on a turn whose
history is still empty AND `bootstrap_active`). step_1 now seeds it up front,
at the START of the first turn, so the write no longer depends on the turn
reaching the persist hook. This module answers ONLY "which greeting, if any" —
the chat row (shape + timestamp) is written by the single writer that owns the
chat table: `chat_module.seed_bootstrap_greeting`.

The gate MUST match the hook's `bootstrap_active`, not merely "has a greeting in
metadata". `agent_metadata["bootstrap_greeting"]` is written once at provision
and never cleared, so gating on it alone would keep this seed candidate alive
for the agent's whole life. `bootstrap_active` expires: Bootstrap.md is
auto-deleted once the agent has ≥ its threshold events. The judgment is shared
with context_runtime via [[lifecycle]].is_bootstrap_active (single source of
truth) so the two writers cannot drift.

Note the writer below this gate carries its own, stronger idempotency
(2026-08-21, Shenzhen-r2 B2): `chat_module.seed_bootstrap_greeting` refuses to
seed once the agent has ANY chat history with this user in ANY instance —
first contact is per-(agent, user), so a new narrative's fresh empty instance
is never re-greeted even while bootstrap is still active. This module's gate
remains the cheap outer filter; the per-agent scope is the writer's contract.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from xyz_agent_context.bootstrap.lifecycle import is_bootstrap_active
from xyz_agent_context.repository.agent_repository import AgentRepository

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


async def resolve_bootstrap_greeting_to_seed(
    db: "AsyncDatabaseClient", agent_id: str, user_id: str
) -> Optional[str]:
    """Return the greeting to seed for this (agent, user), or None to seed nothing.

    None unless ALL hold: the agent exists and is owned by `user_id` (only the
    owner's onboarding is seeded, matching context_runtime), it carries a
    non-empty `bootstrap_greeting`, and bootstrap is still active (Bootstrap.md
    present + under the auto-delete threshold — via the shared
    lifecycle.is_bootstrap_active). Best-effort: any failure returns None,
    leaving ChatModule.hook_persist_turn's prepend as the fallback.
    """
    try:
        agent = await AgentRepository(db).get_agent(agent_id)
        if not agent or not agent.agent_metadata:
            return None
        greeting = agent.agent_metadata.get("bootstrap_greeting")
        if not greeting:
            return None
        # Owner-only: context_runtime injects Bootstrap only when created_by == user.
        if getattr(agent, "created_by", None) != user_id:
            return None
        status = await is_bootstrap_active(db, agent_id, user_id, agent.agent_metadata)
        if not status.active:
            return None
        return greeting
    except Exception as e:  # noqa: BLE001 — best-effort; hook prepend is the fallback
        logger.warning(f"[bootstrap] greeting resolve failed for {agent_id}: {e}")
        return None
