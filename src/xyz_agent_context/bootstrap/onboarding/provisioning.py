"""
@file_name: provisioning.py
@author: Bin Liang
@date: 2026-08-19
@description: Idempotent provisioning of the onboarding guide agent — the
"new user's first agent". Called fire-and-forget from the login paths
(netmind-login, local login, local create-user); never blocks or fails a
login. Mirrors ArenaProvisioningService's shape: random name, awareness
persona, targeted skill install, a pre-created routine — but the routine here
is ACTIVE (the daily check-in IS the product) with a native hard ceiling
(ongoing + max_iterations) instead of a paused consent gate.

Idempotency is USER-level, not agent-level: a write-once
`users.metadata.onboarding_progress.guide_agent_provisioned` marker. An
agent-level marker alone would resurrect the guide agent after the user
deletes it — deleting it must stick.
"""

from __future__ import annotations

import os
import random
from typing import Any, Dict
from uuid import uuid4

from loguru import logger

from xyz_agent_context.utils.deployment_mode import is_cloud_mode

# Env kill-switch. Default ON: merging to dev exercises it on the dev
# deployment immediately and prod picks it up at the next release, with no
# deploy-repo env change; setting "0"/"false"/"no" disables provisioning.
ENV_FLAG = "NARRANEXUS_ONBOARDING_GUIDE_AGENT"

# Written under users.metadata["onboarding_progress"] next to the existing
# checklist booleans (see backend/routes/auth.py::update_onboarding — the
# reader tolerates extra keys). Write-once-true.
ONBOARDING_METADATA_KEY = "onboarding_progress"
GUIDE_METADATA_FLAG = "guide_agent_provisioned"

# Agent-level tag for ops/statistics + a second idempotency line of defense
# (same key Arena uses with value "arena").
PROVISIONED_SOURCE = "onboarding"

GUIDE_SKILL_ID = "narranexus-guide"

CHECKIN_JOB_TITLE = "Daily check-in ☕"
_CHECKIN_JOB = {
    "title": CHECKIN_JOB_TITLE,
    "description": (
        "Once a day, drop by with a fresh topic. Pause or cancel this job "
        "anytime if you'd rather not be pinged."
    ),
    "payload": (
        "Daily check-in time. FIRST read the recent chat history with your "
        "creator. RULES: (1) If your creator has NEVER replied to your last "
        "three consecutive proactive check-ins, send one graceful goodbye "
        "message saying you'll stop reaching out (they can still message you "
        "anytime), then pause THIS job via job_update(status='paused') and do "
        "nothing else. (2) Otherwise send exactly ONE short, lively message in "
        "your creator's language: react to whatever they said last, or open a "
        "fresh interesting topic, and occasionally remind them you can teach "
        "them NarraNexus tricks (read skills/narranexus-guide/SKILL.md first "
        "if they asked about the product). Never send more than one message."
    ),
    "interval_seconds": 86400,
    # Hard ceiling, enforced by JobTrigger regardless of model cooperation:
    # after 14 check-ins the job completes on its own.
    "max_iterations": 14,
    "end_condition": (
        "The user has ignored three consecutive check-ins, or has asked the "
        "agent to stop checking in."
    ),
}


def is_guide_agent_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "1").strip().lower() not in ("0", "false", "no")


async def ensure_guide_agent(db: Any, user_id: str) -> Dict[str, Any]:
    """
    Ensure this user has been offered their onboarding guide agent exactly
    once. Safe to call on every login: cheap skips for the disabled /
    already-provisioned / already-has-agents cases.

    Returns a small status dict for logging/tests; never raises for the
    best-effort steps (skill install, job creation) — the agent row existing
    is what counts as provisioned.
    """
    if not is_guide_agent_enabled():
        return {"skipped": "disabled"}

    from xyz_agent_context.repository import AgentRepository
    from xyz_agent_context.repository.user_repository import UserRepository

    user_repo = UserRepository(db)
    user = await user_repo.get_user(user_id)
    if user is None:
        return {"skipped": "no_user"}

    progress = dict((user.metadata or {}).get(ONBOARDING_METADATA_KEY) or {})
    if progress.get(GUIDE_METADATA_FLAG):
        return {"skipped": "already_provisioned"}

    agent_repo = AgentRepository(db)
    existing = await agent_repo.find(filters={"created_by": user_id})
    if existing:
        # The user already lives here (owns agents) — a proactive stranger
        # would be noise. Mark so we never re-evaluate on later logins.
        # This also covers the tiny concurrent-login race: a second call
        # arriving after the first created the agent lands in this branch.
        await _write_marker(user_repo, user_id)
        return {"skipped": "has_agents"}

    rng = random.Random()
    from xyz_agent_context.bootstrap.naming import generate_name
    from xyz_agent_context.bootstrap.onboarding.personas import (
        pick_persona,
        pick_topic_index,
        render_awareness,
    )

    agent_name = generate_name(rng)
    persona = pick_persona(rng)
    topic_index = pick_topic_index(rng)
    is_local = not is_cloud_mode()

    from xyz_agent_context.bootstrap.provision import provision_new_agent

    agent_id = f"agent_{uuid4().hex[:12]}"
    # bootstrap_profile="none": the shared seam's own apply_bootstrap can't
    # carry ctx.extra (persona/topic/is_local), so we apply the "onboarding"
    # profile ourselves right after — the same split Arena uses.
    result = await provision_new_agent(
        db,
        agent_id=agent_id,
        user_id=user_id,
        agent_name=agent_name,
        agent_description="Your first NarraNexus companion — ask me anything",
        awareness=render_awareness(agent_name, persona, is_local=is_local),
        bootstrap_profile="none",
    )
    warnings = list(result.warnings)

    # Tag the agent for ops/statistics (and as a secondary idempotency key).
    try:
        agent = await agent_repo.get_agent(agent_id)
        meta = dict((agent.agent_metadata if agent else None) or {})
        meta["provisioned_source"] = PROVISIONED_SOURCE
        await agent_repo.update_agent(agent_id, {"agent_metadata": meta})
    except Exception as e:  # noqa: BLE001 — tag is best-effort
        logger.warning(f"[onboarding] tagging {agent_id} failed: {e}")
        warnings.append(f"metadata_tag: {e}")

    # First-run flow with the provision-time random picks.
    try:
        from xyz_agent_context.bootstrap.profiles import (
            BootstrapContext,
            apply_bootstrap,
            get_profile,
        )

        await apply_bootstrap(
            db,
            agent_id=agent_id,
            user_id=user_id,
            profile=get_profile("onboarding"),
            ctx=BootstrapContext(
                agent_id=agent_id,
                user_id=user_id,
                agent_name=agent_name,
                extra={
                    "persona_key": persona["key"],
                    "topic_index": topic_index,
                    "is_local": is_local,
                },
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[onboarding] bootstrap for {agent_id} failed: {e}")
        warnings.append(f"bootstrap: {e}")

    # The guide skill — targeted install (default:false keeps it off ordinary
    # new agents).
    try:
        from xyz_agent_context.marketplace.skill_marketplace_service import (
            SkillMarketplaceService,
        )

        await SkillMarketplaceService().install(agent_id, user_id, GUIDE_SKILL_ID)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[onboarding] guide-skill install for {agent_id} failed: {e}")
        warnings.append(f"guide_skill: {e}")

    # The daily check-in routine — ACTIVE (this proactive touch is the point),
    # bounded by max_iterations + end_condition + the agent's own 3-strikes
    # goodbye. The user can pause/cancel it in the Jobs panel anytime; the
    # greeting says so.
    job_id = None
    try:
        job_id = await _create_checkin_job(db, agent_id, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[onboarding] check-in job for {agent_id} failed: {e}")
        warnings.append(f"checkin_job: {e}")

    await _write_marker(user_repo, user_id)

    logger.info(
        f"[onboarding] guide agent {agent_id} ({agent_name}, "
        f"persona={persona['key']}) provisioned for {user_id}"
        f"{' with warnings: ' + '; '.join(warnings) if warnings else ''}"
    )
    return {
        "provisioned": True,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "persona": persona["key"],
        "job_id": job_id,
        "warnings": warnings,
    }


async def _write_marker(user_repo: Any, user_id: str) -> None:
    """Write-once user-level marker; merge-and-write so sibling metadata keys
    (the onboarding checklist booleans, etc.) are preserved."""
    user = await user_repo.get_user(user_id)
    if user is None:
        return
    metadata = dict(user.metadata or {})
    progress = dict(metadata.get(ONBOARDING_METADATA_KEY) or {})
    if progress.get(GUIDE_METADATA_FLAG):
        return
    progress[GUIDE_METADATA_FLAG] = True
    metadata[ONBOARDING_METADATA_KEY] = progress
    await user_repo.update_user(user_id, {"metadata": metadata})


async def _create_checkin_job(db: Any, agent_id: str, user_id: str) -> str:
    from xyz_agent_context.module.job_module.job_service import JobInstanceService
    from xyz_agent_context.repository.user_repository import UserRepository

    user = await UserRepository(db).get_user(user_id)
    tz = user.timezone if user and getattr(user, "timezone", None) else "UTC"

    result = await JobInstanceService(db).create_job_with_instance(
        agent_id=agent_id,
        user_id=user_id,
        title=_CHECKIN_JOB["title"],
        description=_CHECKIN_JOB["description"],
        job_type="ongoing",
        trigger_config={
            "interval_seconds": _CHECKIN_JOB["interval_seconds"],
            "timezone": tz,
            "max_iterations": _CHECKIN_JOB["max_iterations"],
            "end_condition": _CHECKIN_JOB["end_condition"],
        },
        payload=_CHECKIN_JOB["payload"],
        # Fixed platform spec, not an LLM guess — skip similarity confirmation.
        confirm_new=True,
    )
    if not result.get("success"):
        raise RuntimeError(f"job creation refused: {result}")
    return result["job_id"]
