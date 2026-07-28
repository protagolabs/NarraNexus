"""
@file_name: applier.py
@author: NetMind.AI
@date: 2026-07-21
@description: Migration apply — execute a MigrationPlan onto a NarraNexus agent.

The write half of Agent Migration: given a `MigrationPlan` (from `mapper`),
create (or reuse) an agent and populate it — awareness, general memory, skills,
per-agent url-MCP. The narrative step is NOT executed here (it is agent-driven:
the agent self-summarizes `narrative_instruction` on its first turn); we return
it for the caller to send.

Faithful-reproduction skill policy (Owner): a skill with a `local_path` is
COPIED verbatim into the agent's workspace `skills/<name>/` (migration = copy the
original agent, not substitute a same-name marketplace skill). Only a skill with
no local source falls back to a marketplace install.

Reuses existing repos/factories/services (no HTTP coupling) — callable from the
route or any async context; `user_id` is passed in explicitly.
See reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.migration.mapper import MigrationPlan
from xyz_agent_context.memory import MemoryEngine, MemoryRecord, SCOPE_AGENT
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.repository.instance_repository import InstanceRepository
from xyz_agent_context.repository.instance_awareness_repository import (
    InstanceAwarenessRepository,
)
from xyz_agent_context.repository.mcp_repository import MCPRepository
from xyz_agent_context.module._module_impl.instance_factory import InstanceFactory
from xyz_agent_context.utils.workspace_paths import agent_workspace_path


class ApplyResult(BaseModel):
    agent_id: str
    created: bool
    awareness_written: bool = False
    memory_written: int = 0
    # Default NarraNexus skills (netmind-vision, officecli, ...) provisioned for
    # every new agent — same set a normally-created agent gets.
    default_skills_installed: List[str] = Field(default_factory=list)
    skills_copied: List[str] = Field(default_factory=list)
    skills_installed: List[str] = Field(default_factory=list)
    skills_unmatched: List[str] = Field(default_factory=list)
    mcp_added: List[str] = Field(default_factory=list)
    mcp_stdio_skipped: List[str] = Field(default_factory=list)
    # Passed through for the caller to run as the agent's first turn.
    narrative_instruction: str = ""
    warnings: List[str] = Field(default_factory=list)


async def _awareness_instance_id(db, agent_id: str) -> Optional[str]:
    insts = await InstanceRepository(db).get_by_agent(agent_id, module_class="AwarenessModule")
    return insts[0].instance_id if insts else None


async def _copy_local_skill(agent_id: str, user_id: str, name: str, src: str) -> bool:
    src_path = Path(src).expanduser()
    if not src_path.is_dir():
        return False
    dest = agent_workspace_path(agent_id, user_id) / "skills" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src_path, dest)
    return True


async def apply_plan(
    db,
    user_id: str,
    plan: MigrationPlan,
    agent_id: Optional[str] = None,
) -> ApplyResult:
    """Create/populate an agent from a plan. `db` is an AsyncDatabaseClient."""
    created = False
    if not agent_id:
        agent_id = f"agent_{uuid4().hex[:12]}"
        await AgentRepository(db).add_agent(
            agent_id=agent_id,
            agent_name=plan.agent_name or "Imported Agent",
            created_by=user_id,
            agent_description="Imported via Agent Migration",
            agent_type="chat",
        )
        # provisions AwarenessModule + the other agent-level module instances
        await InstanceFactory(db).create_agent_level_instances(agent_id)
        created = True
    else:
        await InstanceFactory(db).ensure_agent_instances_exist(agent_id)

    result = ApplyResult(
        agent_id=agent_id,
        created=created,
        narrative_instruction=plan.narrative_instruction,
        warnings=list(plan.warnings),
    )

    svc = None  # lazily-created SkillMarketplaceService, shared by steps 0 & 3

    # 0) Default NarraNexus skills — the same is_default set (netmind-vision,
    #    officecli, ...) a normally-created agent gets. Only for a newly-created
    #    agent; degrades to a no-op when the registry is unreachable (desktop
    #    offline). Runs BEFORE the imported skills so a same-name imported skill
    #    still wins (faithful reproduction overwrites the default copy).
    if created:
        try:
            from xyz_agent_context.marketplace.skill_marketplace_service import (
                SkillMarketplaceService,
            )
            svc = SkillMarketplaceService(db)
            summary = await svc.install_defaults(agent_id, user_id)
            result.default_skills_installed = list(summary.get("installed", []))
            if summary.get("failed"):
                logger.warning(f"[migrate.apply] default skills failed={summary['failed']}")
        except Exception as e:  # noqa: BLE001 — defaults must never break import
            logger.warning(f"[migrate.apply] default skills skipped: {e}")

    # 1) Awareness
    if plan.awareness_markdown.strip():
        inst_id = await _awareness_instance_id(db, agent_id)
        if inst_id:
            await InstanceAwarenessRepository(db).upsert(inst_id, plan.awareness_markdown)
            result.awareness_written = True

    # 2) General Memory
    if plan.memory:
        engine = MemoryEngine(db, agent_id)
        for m in plan.memory:
            try:
                await engine.retain(MemoryRecord(
                    agent_id=agent_id, scope_type=SCOPE_AGENT, kind="observation",
                    subtype="world", content_text=m.content, tags=["imported"],
                    proof_count=1,
                    source_ref={"kind": "import", "id": m.source} if m.source else None,
                ))
                result.memory_written += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[migrate.apply] memory retain failed: {e}")

    # 3) Skills — copy the original files verbatim; marketplace only as fallback.
    for skill in plan.skills:
        try:
            if skill.local_path and await _copy_local_skill(agent_id, user_id, skill.name, skill.local_path):
                result.skills_copied.append(skill.name)
                continue
            # fallback: same-name marketplace install
            if svc is None:
                from xyz_agent_context.marketplace.skill_marketplace_service import (
                    SkillMarketplaceService,
                )
                svc = SkillMarketplaceService(db)
            found = await svc.search(q=skill.name, limit=10)
            match = next(
                (it for it in found.get("items", [])
                 if str(it.get("name", "")).strip().lower() == skill.name.strip().lower()),
                None,
            )
            if match and match.get("skill_id"):
                await svc.install(agent_id, user_id, match["skill_id"])
                result.skills_installed.append(skill.name)
            else:
                result.skills_unmatched.append(skill.name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[migrate.apply] skill '{skill.name}' failed: {e}")
            result.skills_unmatched.append(skill.name)

    # 4) url-MCP (stdio deferred — recorded so the caller can surface it)
    if plan.mcp_url_servers:
        mrepo = MCPRepository(db)
        for srv in plan.mcp_url_servers:
            try:
                await mrepo.add_mcp(
                    agent_id=agent_id, user_id=user_id,
                    mcp_id=f"mcp_{uuid4().hex[:8]}", name=srv.name,
                    url=srv.url or "", headers=srv.headers or None,
                    description=f"imported ({srv.transport})", is_enabled=True,
                )
                result.mcp_added.append(srv.name)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[migrate.apply] mcp '{srv.name}' failed: {e}")
    result.mcp_stdio_skipped = [s.name for s in plan.mcp_stdio_servers]

    logger.info(
        f"[migrate.apply] agent={agent_id} created={created} "
        f"awareness={result.awareness_written} memory={result.memory_written} "
        f"defaults={len(result.default_skills_installed)} "
        f"skills_copied={len(result.skills_copied)} installed={len(result.skills_installed)} "
        f"unmatched={len(result.skills_unmatched)} mcp={len(result.mcp_added)}"
    )
    return result
