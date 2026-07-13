"""
@file_name: office_module.py
@author: rujing.yan
@date: 2026-07-13
@description: OfficeModule — lets the agent create/read/edit Word, Excel, and
PowerPoint files via the OfficeCLI binary, and preview them as artifact tabs.

A hot-pluggable capability module (like SkillModule): no database, no per-agent
credential. State lives in the agent workspace as .docx/.xlsx/.pptx files. Two
MCP tools (port 7810):
- office_cli   : passthrough to the officecli binary (create/view/edit)
- office_render: render a document to a sibling HTML preview + register it as an
                 artifact so the user can view and download it

OfficeCLI (https://github.com/iOfficeAI/OfficeCLI) is a self-contained binary —
no Microsoft Office required — shipped like lark-cli via npm (@officecli/officecli).
"""

from typing import List, Optional

from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.schema import ContextData, MCPServerConfig, ModuleConfig
from xyz_agent_context.utils import DatabaseClient


OFFICE_MCP_PORT = 7810


OFFICE_INSTRUCTIONS = (
    "## Office documents (Word / Excel / PowerPoint)\n"
    "\n"
    "You can create, read, and edit .docx / .xlsx / .pptx files with the "
    "`office_cli` tool (backed by OfficeCLI — no Microsoft Office needed) and "
    "surface them to the user with `office_render`.\n"
    "\n"
    "- Keep each document in its own **subdirectory** of your workspace "
    "(e.g. `office/q4-report/report.docx`), never at the workspace root — "
    "that is what lets the preview render.\n"
    "- Build/edit with `office_cli` (pass officecli arguments as one string, "
    "e.g. `create office/deck/slides.pptx`, then `add ... --prop title=...`).\n"
    "- When the document is ready (or after edits), call `office_render` on it "
    "to show a high-fidelity preview tab the user can view and download. Call "
    "`office_render` again with `target_artifact_id` to refresh the same tab "
    "after further edits.\n"
    "- Prefer real office documents over pasting big tables/text into chat when "
    "the user wants a Word/Excel/PowerPoint deliverable."
)


class OfficeModule(XYZBaseModule):
    """Office document authoring + preview capability (OfficeCLI-backed)."""

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = OFFICE_MCP_PORT
        self.instructions = OFFICE_INSTRUCTIONS

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="OfficeModule",
            priority=80,
            enabled=True,
            description="Create, edit, and preview Word/Excel/PowerPoint files via OfficeCLI.",
            module_type="capability",
        )

    async def get_instructions(self, ctx_data: ContextData) -> str:
        # Static instructions (no ctx_data interpolation) — return directly to
        # avoid str.format touching any literal braces in officecli examples.
        return self.instructions

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="office_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse",
        )

    def create_mcp_server(self):
        """Create the MCP Server (delegates to _office_impl)."""
        from xyz_agent_context.module.office_module._office_impl._office_mcp_tools import (
            create_office_mcp_server,
        )
        return create_office_mcp_server(self.port)
