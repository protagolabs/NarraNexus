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
    # ⚠️ FRONTEND COUPLING — tool names are matched by the UI.
    #
    # `create_artifact` and `upload_artifact_file` calls are recognised in
    # the agent event stream by `frontend/src/components/chat/ChatPanel.tsx`
    # (`isArtifactToolName` / `ARTIFACT_TOOL_BASE_NAMES`). The frontend
    # matches the BARE name as a suffix — it tolerates the fully-qualified
    # `mcp__common_tools_module__<tool>` form the stream actually carries.
    #
    # That match is what drives the artifact panel's *live* discovery (the
    # artifact tab appearing during/right after a run). If you rename either
    # tool here, you MUST update `ARTIFACT_TOOL_BASE_NAMES` in ChatPanel.tsx
    # in the same change — otherwise created artifacts silently stop showing
    # up until an unrelated reload (e.g. switching agents).

    @mcp.tool(
        name="create_artifact",
        description=(
            "Create or update a visual artifact — a rich tab shown right next to "
            "the chat (interactive charts, styled HTML pages, formatted "
            "reports/tables). Artifacts render at full fidelity and look great — "
            "far better than dumping numbers or ASCII tables into a chat message. "
            "Reach for this proactively: whenever a chart, page, table or report "
            "would make your answer clearer, create the artifact directly as part "
            "of doing the task — you don't need to set it up or announce it first.\n"
            "\n"
            "Pass the content INLINE via the `content` argument. Do NOT write the "
            "HTML/JSON/markdown to a workspace file first and then create_artifact "
            "from it — that makes you generate the exact same content twice. This "
            "tool IS the way to deliver the content; one step, not two.\n"
            "\n"
            "kind — pass one of these exact values:\n"
            "  application/vnd.echarts+json  an ECharts `option` object as JSON; "
            "prefer this for any numbers, trends, comparisons or distributions\n"
            "  text/html   a self-contained HTML page; inline JS and CDN assets "
            "(web fonts, CSS) are fine — embed your data in the page, don't fetch "
            "it at runtime\n"
            "  text/markdown   a formatted report\n"
            "  text/csv   tabular data\n"
            "content — the full text payload for that kind.\n"
            "title — a short, human-readable tab title.\n"
            "target_artifact_id — pass to update an existing tab in place (kind "
            "must match); omit to create a new tab.\n"
            "\n"
            "On success returns {artifact_id, version, url}; the tab is already "
            "visible to the user, so don't repeat the URL in your reply. On "
            "failure returns {error, code} — the error text states the cause; "
            "fix the inputs and call again. A failed create_artifact never blocks "
            "you and is safe to retry."
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
        """Register-scoped MCP handler for `create_artifact`.

        The LLM-facing contract lives in the `description=` above. This
        body just resolves a DB client and delegates to
        `artifact_runner.create_text_artifact`; all validation, quota and
        filesystem logic is there. Every failure path returns a structured
        `{error, code}` dict — see the error-handling note below.
        """
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
            # Expected, structured rejection (bad kind, quota, not-found, ...).
            # The message is already actionable; hand it straight to the LLM.
            logger.warning(f"create_artifact rejected: {e}")
            return {"error": str(e), "code": e.code}
        except Exception as e:  # noqa: BLE001
            # Unexpected failure (DB hiccup, disk error, ...). NEVER let it
            # propagate as an unhandled MCP exception — that surfaces to the
            # agent as an opaque tool crash and can stall the loop. Return a
            # structured, retryable error so the agent reads the cause and
            # simply calls the tool again.
            logger.exception(f"create_artifact failed unexpectedly: {e}")
            return {
                "error": f"create_artifact failed unexpectedly: {e}. "
                         f"This is likely transient — you can call the tool again.",
                "code": 500,
            }

    # ⚠️ FRONTEND COUPLING — see the note above register()'s first tool.
    # Renaming this tool requires updating ARTIFACT_TOOL_BASE_NAMES in
    # frontend/src/components/chat/ChatPanel.tsx in the same change.
    @mcp.tool(
        name="upload_artifact_file",
        description=(
            "Show a binary file from the agent workspace to the user as a visual "
            "artifact tab (PNG / JPEG / PDF). Use this for files that already "
            "exist on disk — e.g. an image or PDF you generated with another "
            "tool. For text-based content (HTML / charts / markdown / CSV) use "
            "create_artifact instead and pass the content inline.\n"
            "\n"
            "local_path — absolute path inside the agent workspace.\n"
            "kind — one of: image/png, image/jpeg, application/pdf.\n"
            "title — a short, human-readable tab title.\n"
            "target_artifact_id — pass to update an existing tab in place (kind "
            "must match); omit to create a new tab.\n"
            "\n"
            "On failure returns {error, code} — the error text states the cause "
            "(wrong kind, path outside the workspace, file too large, ...); fix "
            "it and call again. A failed call never blocks you and is safe to retry."
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
        """Register-scoped MCP handler for `upload_artifact_file`.

        LLM-facing contract is in `description=` above. Delegates to
        `artifact_runner.upload_binary_artifact` (path-escape check, size
        check, DB write live there). Every failure path returns a
        structured `{error, code}` dict.
        """
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
        except Exception as e:  # noqa: BLE001
            # Same contract as create_artifact: never propagate an unhandled
            # exception — return a structured, retryable error instead.
            logger.exception(f"upload_artifact_file failed unexpectedly: {e}")
            return {
                "error": f"upload_artifact_file failed unexpectedly: {e}. "
                         f"This is likely transient — you can call the tool again.",
                "code": 500,
            }
