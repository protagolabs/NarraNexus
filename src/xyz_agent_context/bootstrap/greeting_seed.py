"""
@file_name: greeting_seed.py
@author: Bin Liang
@date: 2026-08-20
@description: Seed the bootstrap greeting as the FIRST persisted assistant
message of a brand-new chat instance.

Background — the bootstrap greeting used to reach DB history only lazily, when
ChatModule.hook_persist_turn ran on the user's first REPLY (it prepends the
greeting when history is still empty). Before that first reply the frontend
showed a client-only overlay bubble that carried no agent identity, so its
avatar fell back to the generic "AI" label and the message was absent from
history. This seeds the greeting the moment a chat instance is born (step_1's
_ensure_user_chat_instance), so a new agent's history opens with a real,
correctly-attributed assistant message.

The lazy prepend in ChatModule.hook_persist_turn stays as the fallback for
agents created before this path; it auto-skips here because history is no
longer empty (`len(messages) != 0`) — no double write. Bootstrap.md, the
injection prompts, and the greeting metadata are untouched.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from loguru import logger

from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.repository.event_memory_repository import EventMemoryRepository

# ChatModule.config.name — the repository maps it to instance_json_format_memory_chat.
_CHAT_MODULE_NAME = "ChatModule"


async def seed_first_greeting_message(
    db, agent_id: str, user_id: str, instance_id: str
) -> bool:
    """Write the bootstrap greeting as the first assistant message of `instance_id`.

    Returns True iff a greeting row was written. No-op (False) unless the agent
    is still bootstrapping (``Bootstrap.md`` present) AND carries a non-empty
    ``bootstrap_greeting`` in its metadata — the same guard ChatModule uses, so
    an agent whose bootstrap already auto-deleted is never re-greeted.

    Timestamp anchor = the agent's creation time, which is always earlier than
    any turn, so a timestamp-ascending sort (chat-history API + frontend
    timeline) keeps the greeting strictly before the user's first message — the
    invariant tests/chat_module/test_bootstrap_greeting_order locks in.

    Best-effort: any failure logs and returns False, leaving the ChatModule
    hook_persist_turn prepend as the fallback.
    """
    try:
        agent = await AgentRepository(db).get_agent(agent_id)
        if not agent or not agent.agent_metadata:
            return False
        greeting = agent.agent_metadata.get("bootstrap_greeting")
        if not greeting:
            return False

        # Bootstrap active == Bootstrap.md present (mirrors provision.py:197 and
        # ChatModule's bootstrap_active). Guards against re-greeting an agent
        # whose bootstrap has already auto-deleted.
        from xyz_agent_context.settings import settings
        from xyz_agent_context.utils.workspace_paths import agent_workspace_path

        workspace_path = agent_workspace_path(
            agent_id, user_id, base=settings.base_working_path
        )
        if not os.path.isfile(str(workspace_path / "Bootstrap.md")):
            return False

        # Timestamp MUST be earlier than the user's first message (stamped
        # event.created_at = turn start). agent_create_time predates any turn.
        # Do NOT fall back to utc_now(): this runs mid-turn, so now() is LATER
        # than the user message and would render the greeting after the query —
        # the P0 that test_bootstrap_greeting_order guards. With no reliable
        # pre-turn anchor, defer to ChatModule.hook_persist_turn, which stamps
        # event.created_at - 1ms correctly.
        anchor = getattr(agent, "agent_create_time", None)
        if anchor is None:
            return False
        ts_iso = anchor.isoformat()
        memory: Dict[str, Any] = {
            "messages": [
                {
                    "role": "assistant",
                    "content": greeting,
                    "meta_data": {
                        "timestamp": ts_iso,
                        "instance_id": instance_id,
                        "bootstrap": True,
                    },
                }
            ],
            "updated_at": ts_iso,
        }
        ok = await EventMemoryRepository(
            agent_id, user_id, db
        ).add_instance_json_format_memory(_CHAT_MODULE_NAME, instance_id, memory)
        if ok:
            logger.info(
                f"[bootstrap] seeded greeting into new chat instance "
                f"{instance_id} (agent={agent_id})"
            )
        return bool(ok)
    except Exception as e:  # noqa: BLE001 — best-effort; ChatModule prepend is the fallback
        logger.warning(
            f"[bootstrap] greeting seed failed for {agent_id}/{instance_id}: {e}"
        )
        return False
