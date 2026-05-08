"""
@file_name: artifact_tool.py
@author: Bin Liang
@date: 2026-05-08
@description: Register `create_artifact` + `upload_artifact_file` MCP tools on the
common_tools_module FastMCP server. Each call resolves the per-agent context from
the MCP request headers, opens a fresh DB client, and delegates to artifact_runner.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.module.common_tools_module._common_tools_impl import artifact_runner
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.db_factory import get_db_client


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="create_artifact",
        description=(
            "Create or iterate a visual artifact tab for the user. Use for text-based "
            "outputs: HTML apps, echarts JSON configs, csv tables, markdown reports. "
            "Pass target_artifact_id to iterate an existing tab (kind must match). "
            "Returns a URL the user can already see in their UI — no need to send the "
            "URL in your reply."
        ),
    )
    async def create_artifact(
        kind: str,
        content: str,
        title: str,
        agent_id: str,
        user_id: str,
        session_id: Optional[str] = None,
        description: Optional[str] = None,
        target_artifact_id: Optional[str] = None,
    ) -> dict:
        try:
            db = await get_db_client()
            repo = ArtifactRepository(db)
            result = await artifact_runner.create_text_artifact(
                repo=repo,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                kind=kind,                                  # type: ignore[arg-type]
                content=content,
                title=title,
                description=description,
                target_artifact_id=target_artifact_id,
            )
            return result.model_dump(mode="json")
        except artifact_runner.ArtifactError as e:
            logger.warning(f"create_artifact rejected: {e}")
            return {"error": str(e), "code": e.code}

    @mcp.tool(
        name="upload_artifact_file",
        description=(
            "Upload a binary file (PNG / JPEG / PDF) from your workspace as an artifact "
            "tab. local_path must be inside the agent workspace. Pass target_artifact_id "
            "to iterate an existing tab (kind must match)."
        ),
    )
    async def upload_artifact_file(
        local_path: str,
        kind: str,
        title: str,
        agent_id: str,
        user_id: str,
        session_id: Optional[str] = None,
        description: Optional[str] = None,
        target_artifact_id: Optional[str] = None,
    ) -> dict:
        try:
            db = await get_db_client()
            repo = ArtifactRepository(db)
            result = await artifact_runner.upload_binary_artifact(
                repo=repo,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                kind=kind,                                  # type: ignore[arg-type]
                local_path=local_path,
                title=title,
                description=description,
                target_artifact_id=target_artifact_id,
            )
            return result.model_dump(mode="json")
        except artifact_runner.ArtifactError as e:
            logger.warning(f"upload_artifact_file rejected: {e}")
            return {"error": str(e), "code": e.code}
