"""
@file_name: _office_mcp_tools.py
@author: rujing.yan
@date: 2026-07-13
@description: OfficeModule MCP Server tool definitions.

Two stateless tools (each receives agent_id + user_id, injected by the runtime):

- office_cli(command): passthrough to the officecli binary, run in the agent
  workspace. Full create/view/edit power without enumerating every subcommand.
- office_render(path): render an office file to a sibling HTML preview via
  OfficeCLI, then register it as an artifact (entry = the original .docx/.xlsx/
  .pptx, so "download original" works) through the SHARED registration service
  (xyz_agent_context.artifact.registration) — NOT by importing another Module.

⚠️ FRONTEND COUPLING: office_render is recognised as an artifact-producing tool
by ChatPanel.tsx (ARTIFACT_TOOL_BASE_NAMES). It returns ``artifact_id`` at the
top level so the panel's live discovery picks up the new tab. Rename the tool →
update that constant in the same change.
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.artifact import registration
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.db_factory import get_db_client

from .officecli_client import OfficeCLIClient
from ._office_command_security import sanitize_command

# One shared, stateless client for the whole MCP server.
_client = OfficeCLIClient()


_OFFICE_CLI_DESCRIPTION = (
    "Drive OfficeCLI to read, create, and edit Word (.docx), Excel (.xlsx), and "
    "PowerPoint (.pptx) files — no Microsoft Office needed. Runs in your agent "
    "workspace, so use workspace-relative paths.\n"
    "\n"
    "IMPORTANT — put each document in its own SUBDIRECTORY (e.g. "
    "office/q4-report/report.docx), NOT at the workspace root. That is what lets "
    "office_render show a live preview afterwards.\n"
    "\n"
    "Pass the officecli arguments as a single string, e.g.:\n"
    "  create office/deck/slides.pptx\n"
    "  add office/deck/slides.pptx / --type slide --prop title=\"Q4 Report\"\n"
    "  view office/report/report.docx outline\n"
    "  set office/report/report.docx /body/p[1]/r[1] --prop bold=true\n"
    "  view office/budget/budget.xlsx text --cols A,B,C\n"
    "\n"
    "Add --json to any command for structured output. After you finish editing a "
    "document, call office_render on it to surface a preview tab to the user. "
    "Blocked subcommands: install, config, mcp, watch (use office_render for a "
    "preview). Returns {success, data} or {success:false, error}."
)

_OFFICE_RENDER_DESCRIPTION = (
    "Render a Word/Excel/PowerPoint file to a high-fidelity HTML preview and show "
    "it as a visual tab next to the chat. Use this after creating or editing an "
    "office document so the user can SEE it (and download the original file).\n"
    "\n"
    "path — workspace-relative path to the .docx/.xlsx/.pptx you created (must be "
    "inside a subdirectory, e.g. office/deck/slides.pptx — a file at the workspace "
    "root cannot be previewed).\n"
    "title — optional tab title (defaults to the filename).\n"
    "target_artifact_id — pass to refresh an existing tab in place after further "
    "edits; omit to create a new tab.\n"
    "\n"
    "Returns {success, artifact_id, url, preview}; the tab is already visible so "
    "don't repeat the URL. On failure returns {success:false, error} — the message "
    "states the cause (path outside workspace, file at root, missing file); fix and "
    "retry."
)


def create_office_mcp_server(port: int) -> FastMCP:
    """Create the OfficeModule MCP Server (SSE) with the office tools registered."""
    mcp = FastMCP("office_module")
    mcp.settings.port = port

    @mcp.tool(name="office_cli", description=_OFFICE_CLI_DESCRIPTION)
    async def office_cli(agent_id: str, user_id: str, command: str) -> dict:
        try:
            args = sanitize_command(command)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        return await _client.run(agent_id, user_id, args)

    @mcp.tool(name="office_render", description=_OFFICE_RENDER_DESCRIPTION)
    async def office_render(
        agent_id: str,
        user_id: str,
        path: str,
        title: str = "",
        session_id: Optional[str] = None,
        target_artifact_id: Optional[str] = None,
    ) -> dict:
        rendered = await _client.render_preview(agent_id, user_id, path)
        if not rendered.get("success"):
            return rendered

        try:
            db = await get_db_client()
            repo = ArtifactRepository(db)
            result = await registration.register_artifact(
                repo=repo,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                kind=rendered["kind"],  # type: ignore[arg-type]
                entry_path=rendered["office_abs"],
                title=title or os.path.basename(rendered["office_abs"]),
                description=None,
                target_artifact_id=target_artifact_id,
            )
            return {
                "success": True,
                "artifact_id": result.artifact_id,
                "url": result.url,
                "preview": rendered["preview_rel"],
            }
        except registration.ArtifactError as e:
            logger.warning(f"office_render register rejected: {e}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:  # noqa: BLE001
            logger.exception(f"office_render failed unexpectedly: {e}")
            return {
                "success": False,
                "error": f"office_render failed unexpectedly: {e}. Likely transient — retry.",
                "code": 500,
            }

    return mcp
