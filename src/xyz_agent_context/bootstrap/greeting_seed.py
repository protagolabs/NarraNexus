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
and never cleared, so gating on it alone would re-greet EVERY new narrative the
agent opens for its whole life (each new narrative gets a fresh empty chat
instance, and the writer's idempotency guard is per-instance). `bootstrap_active`
expires: Bootstrap.md is auto-deleted once the agent has ≥ its threshold events,
after which neither the hook nor this seed should greet. We recompute it here the
same way context_runtime does (owner-only, read-side workspace resolver,
Bootstrap.md present, event_count < threshold) so the two writers stay in lockstep.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from loguru import logger

from xyz_agent_context.bootstrap.profiles import auto_delete_threshold_from_meta
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


async def _bootstrap_active(
    db: "AsyncDatabaseClient", agent_id: str, user_id: str, agent_metadata: dict
) -> bool:
    """Mirror of context_runtime's bootstrap_active: Bootstrap.md still present
    for the owner AND the agent is under its auto-delete event threshold.

    Uses the READ-side workspace resolver (handles legacy flat layouts) so the
    Bootstrap.md lookup matches what the runtime and auto-delete use — a
    write-side resolver would miss the file on migrated agents and silently stop
    seeding (or, worse, disagree with the hook)."""
    from xyz_agent_context.settings import settings

    bootstrap_path = os.path.join(
        str(resolve_existing_workspace(agent_id, user_id, settings.base_working_path)),
        "Bootstrap.md",
    )
    if not os.path.isfile(bootstrap_path):
        return False
    threshold = auto_delete_threshold_from_meta(agent_metadata)
    if threshold is None:
        return True  # semantic-only profile — never auto-deletes
    try:
        rows = await db.execute(
            "SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        event_count = rows[0]["cnt"] if rows else 0
    except Exception:  # noqa: BLE001 — count failure → treat as active (fail-open, like the runtime)
        event_count = 0
    return event_count < threshold


async def resolve_bootstrap_greeting_to_seed(
    db: "AsyncDatabaseClient", agent_id: str, user_id: str
) -> Optional[str]:
    """Return the greeting to seed for this (agent, user), or None to seed nothing.

    None unless ALL hold: the agent exists and is owned by `user_id` (only the
    owner's onboarding is seeded, matching context_runtime), it carries a
    non-empty `bootstrap_greeting`, and bootstrap is still active (Bootstrap.md
    present + under the auto-delete threshold). Best-effort: any failure returns
    None, leaving ChatModule.hook_persist_turn's prepend as the fallback.
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
        if not await _bootstrap_active(db, agent_id, user_id, agent.agent_metadata):
            return None
        return greeting
    except Exception as e:  # noqa: BLE001 — best-effort; hook prepend is the fallback
        logger.warning(f"[bootstrap] greeting resolve failed for {agent_id}: {e}")
        return None
