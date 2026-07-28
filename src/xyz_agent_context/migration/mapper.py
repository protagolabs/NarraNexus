"""
@file_name: mapper.py
@author: NetMind.AI
@date: 2026-07-21
@description: Migration mapping — the "convert" step (standardized JSON → a
concrete write plan for a NarraNexus agent).

Pure + side-effect-free: `build_plan(StandardizedAgentImport) -> MigrationPlan`
turns the scanner's framework-agnostic JSON into the exact operations a consumer
executes:
- Awareness  ← agent.system_prompt (the imported persona/instructions)
- General Memory ← memory[]  (written via the `memory_retain` MCP tool)
- Skills     ← skills[]      (name-matched against the Skill Marketplace)
- MCP        ← mcp_servers[] transport=url  (importable now via the mcp API);
              transport=stdio deferred to local-mode wiring (v1.1)
- Narrative  ← session_summary_seed  (the agent SELF-summarizes it via
              `create_narrative` — Owner's flow; not a bulk history import)

Both consumers (Import Button backend, Migration Skill) build the same plan.
See reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from xyz_agent_context.schema.migration_schema import (
    MigrationMcpServer,
    StandardizedAgentImport,
)


class PlannedMemory(BaseModel):
    content: str
    source: str = ""


class PlannedSkill(BaseModel):
    name: str
    # Source dir to copy verbatim (faithful reproduction). None → try marketplace.
    local_path: Optional[str] = None


class MigrationPlan(BaseModel):
    agent_name: str = ""
    # The imported instructions become the agent's Awareness (Owner decision:
    # CLAUDE.md/SOUL.md/AGENTS.md → Awareness, not Memory).
    awareness_markdown: str = ""
    memory: List[PlannedMemory] = Field(default_factory=list)
    # Skills to reproduce. `local_path` set → copy the files verbatim (faithful
    # migration); else fall back to a same-name Skill Marketplace install.
    skills: List[PlannedSkill] = Field(default_factory=list)
    # url-MCP servers importable now (name/url/headers).
    mcp_url_servers: List[MigrationMcpServer] = Field(default_factory=list)
    # stdio-MCP servers captured but NOT wired yet (need local-mode data-model
    # extension, v1.1) — surfaced so the user sees them.
    mcp_stdio_servers: List[MigrationMcpServer] = Field(default_factory=list)
    # If the source had sessions, the instruction the agent runs to self-author
    # a Narrative summarizing the imported context.
    narrative_instruction: str = ""
    # Human-facing warnings for the preview (secrets, unsupported, unmapped).
    warnings: List[str] = Field(default_factory=list)


_NARRATIVE_INSTR = (
    "Summarize the imported context below in your own words as a concise memory "
    "thread, then call create_narrative(title, description) to file it as your "
    "starting Narrative. Do not copy it verbatim — capture the gist, ongoing "
    "goals, and who the user is.\n\n=== imported session context ===\n{seed}"
)


def build_plan(imp: StandardizedAgentImport) -> MigrationPlan:
    plan = MigrationPlan(
        agent_name=imp.agent.name or "Imported Agent",
        awareness_markdown=imp.agent.system_prompt or "",
        memory=[PlannedMemory(content=m.content, source=m.source_file) for m in imp.memory],
        skills=[PlannedSkill(name=s.name, local_path=s.local_path) for s in imp.skills],
    )

    for srv in imp.mcp_servers:
        if srv.transport == "url":
            plan.mcp_url_servers.append(srv)
        else:
            plan.mcp_stdio_servers.append(srv)
        if srv.secret_fields:
            plan.warnings.append(
                f"MCP '{srv.name}' carries secrets in {', '.join(srv.secret_fields)} "
                f"— shown in plaintext; confirm before importing."
            )

    if plan.mcp_stdio_servers:
        names = ", ".join(s.name for s in plan.mcp_stdio_servers)
        plan.warnings.append(
            f"{len(plan.mcp_stdio_servers)} stdio MCP server(s) ({names}) are captured "
            f"but not imported yet (local-mode wiring is v1.1); set up equivalents manually."
        )

    if imp.session_summary_seed.strip():
        plan.narrative_instruction = _NARRATIVE_INSTR.format(seed=imp.session_summary_seed)

    if imp.custom.unmapped_files:
        plan.warnings.append(
            f"{len(imp.custom.unmapped_files)} unmapped file(s): "
            f"{', '.join(imp.custom.unmapped_files[:8])}"
        )
    if imp.custom.credential_keys:
        plan.warnings.append(
            f"{len(imp.custom.credential_keys)} credential key(s) were NOT imported "
            f"(re-enter in Settings): {', '.join(imp.custom.credential_keys[:8])}"
        )
    if imp.source.framework == "custom":
        plan.warnings.append("Custom/unknown framework — review the mapping carefully.")

    return plan
