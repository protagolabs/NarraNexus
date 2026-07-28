"""
Migration applier — execute a plan onto a NarraNexus agent (create + populate).

Uses the shared db_client fixture (in-memory sqlite, migrated). Workspace base
is redirected to tmp so the skill file-copy is isolated. No network: only the
local-skill-copy path is exercised (marketplace fallback needs a live registry).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.migration.applier import apply_plan
from xyz_agent_context.migration.mapper import build_plan
from xyz_agent_context.schema.migration_schema import (
    MigrationAgent,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSkill,
    MigrationSource,
    StandardizedAgentImport,
)
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.repository.instance_repository import InstanceRepository
from xyz_agent_context.repository.instance_awareness_repository import (
    InstanceAwarenessRepository,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    return tmp_path


def _import_with_local_skill(skill_dir: Path) -> StandardizedAgentImport:
    (skill_dir / "SKILL.md").write_text("# my skill", encoding="utf-8")
    return StandardizedAgentImport(
        source=MigrationSource(framework="claude_code", detected_path="/x", detection_confidence="high"),
        agent=MigrationAgent(name="Imported One", system_prompt="You are imported."),
        skills=[MigrationSkill(name="myskill", source="claude_code", local_path=str(skill_dir))],
        memory=[
            MigrationMemory(type="fact", content="user prefers Python", source_file="MEMORY.md"),
            MigrationMemory(type="fact", content="lives in Shanghai", source_file="MEMORY.md"),
        ],
        mcp_servers=[MigrationMcpServer(name="remote", transport="url", url="https://h/sse",
                                        headers={"Authorization": "Bearer x"})],
        session_summary_seed="we planned the Q3 roadmap",
    )


@pytest.mark.asyncio
async def test_apply_creates_and_populates_agent(db_client, workspace, tmp_path):
    skill_src = tmp_path / "src_skill"
    skill_src.mkdir()
    imp = _import_with_local_skill(skill_src)
    plan = build_plan(imp)

    res = await apply_plan(db_client, user_id="user_x", plan=plan)

    # agent created
    assert res.created is True and res.agent_id.startswith("agent_")
    agent = await AgentRepository(db_client).get_agent(res.agent_id)
    assert agent is not None and agent.agent_name == "Imported One"

    # awareness written to the AwarenessModule instance
    assert res.awareness_written is True
    insts = await InstanceRepository(db_client).get_by_agent(res.agent_id, module_class="AwarenessModule")
    aw = await InstanceAwarenessRepository(db_client).get_by_instance(insts[0].instance_id)
    assert "You are imported." in aw.awareness

    # memory: both facts retained
    assert res.memory_written == 2

    # skill copied verbatim into workspace/skills/<name>/
    assert res.skills_copied == ["myskill"]
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path
    copied = agent_workspace_path(res.agent_id, "user_x") / "skills" / "myskill" / "SKILL.md"
    assert copied.exists()

    # url-mcp added; narrative instruction passed through
    assert res.mcp_added == ["remote"]
    assert "create_narrative" in res.narrative_instruction and "Q3 roadmap" in res.narrative_instruction


@pytest.mark.asyncio
async def test_apply_no_local_source_marks_unmatched(db_client, workspace, monkeypatch):
    # a skill with no local_path and a marketplace that returns nothing → unmatched
    imp = StandardizedAgentImport(
        source=MigrationSource(framework="codex", detected_path="/x", detection_confidence="high"),
        agent=MigrationAgent(name="A", system_prompt="hi"),
        skills=[MigrationSkill(name="ghost-skill")],
    )
    plan = build_plan(imp)

    async def _empty_search(*a, **k):
        return {"items": []}
    import xyz_agent_context.marketplace.skill_marketplace_service as sms
    monkeypatch.setattr(sms.SkillMarketplaceService, "search", _empty_search)

    res = await apply_plan(db_client, user_id="user_x", plan=plan)
    assert res.skills_unmatched == ["ghost-skill"]
    assert res.skills_copied == [] and res.skills_installed == []
