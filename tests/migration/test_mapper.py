"""
Migration mapper — standardized JSON → concrete write plan (the convert step).
"""
from __future__ import annotations

from xyz_agent_context.migration.mapper import build_plan
from xyz_agent_context.schema.migration_schema import (
    MigrationAgent,
    MigrationCustom,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSkill,
    MigrationSource,
    StandardizedAgentImport,
)


def _imp(**over) -> StandardizedAgentImport:
    base = dict(
        source=MigrationSource(framework="claude_code", detected_path="/x", detection_confidence="high"),
        agent=MigrationAgent(name="Neo", system_prompt="You are Neo."),
        skills=[MigrationSkill(name="web-search"), MigrationSkill(name="pdf")],
        memory=[MigrationMemory(type="fact", content="user likes tea", source_file="MEMORY.md")],
        mcp_servers=[
            MigrationMcpServer(name="remote", transport="url", url="https://h/sse",
                               headers={"Authorization": "Bearer x"}, secret_fields=["headers.Authorization"]),
            MigrationMcpServer(name="fs", transport="stdio", command="npx", args=["-y", "srv"]),
        ],
        session_summary_seed="we discussed the Q3 roadmap",
        custom=MigrationCustom(credential_keys=["OPENAI_API_KEY"]),
    )
    base.update(over)
    return StandardizedAgentImport(**base)


def test_plan_core_mapping():
    p = build_plan(_imp())
    assert p.agent_name == "Neo"
    assert p.awareness_markdown == "You are Neo."          # system_prompt → Awareness
    assert p.skill_names == ["web-search", "pdf"]
    assert [m.content for m in p.memory] == ["user likes tea"]


def test_plan_splits_mcp_url_vs_stdio():
    p = build_plan(_imp())
    assert [s.name for s in p.mcp_url_servers] == ["remote"]   # importable now
    assert [s.name for s in p.mcp_stdio_servers] == ["fs"]     # deferred
    # stdio not-imported warning + secret warning both present
    assert any("stdio MCP" in w for w in p.warnings)
    assert any("secrets" in w and "remote" in w for w in p.warnings)


def test_plan_narrative_instruction_from_seed():
    p = build_plan(_imp())
    assert "create_narrative" in p.narrative_instruction
    assert "Q3 roadmap" in p.narrative_instruction


def test_plan_no_seed_no_narrative():
    p = build_plan(_imp(session_summary_seed=""))
    assert p.narrative_instruction == ""


def test_plan_credential_and_custom_warnings():
    p = build_plan(_imp(
        source=MigrationSource(framework="custom", detected_path="/x", detection_confidence="low"),
    ))
    assert any("credential key" in w for w in p.warnings)
    assert any("Custom/unknown framework" in w for w in p.warnings)
