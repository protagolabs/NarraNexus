"""
@file_name: _general_memory_mcp_tools.py
@author: NetMind.AI
@date: 2026-06-03
@description: The agent-facing memory tools — `remember`, `grep_memory`,
`memory_retain`.

These are the unified "回忆" surface (design §6.3): one cross-kind ranked
recall + one cross-kind exact/regex search + one explicit durable write,
replacing the fragmented per-module recall tools (view_narrative /
search_social_network / get_chat_history / …). `remember` and `memory_retain`
route through the AgentDataStore seam (DirectStore locally / HttpStore in
cloud — see module/data_access); `grep_memory` still calls MemoryCoordinator
directly (its HTTP twin refuses regex, so it waits for a timeout-safe engine).
`agent_id` is a tool parameter (the LLM passes its own id — same convention as
every other module's tools).
"""
from __future__ import annotations

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine, format_memory_hits
from xyz_agent_context.module.base import XYZBaseModule


def create_general_memory_mcp_server(port: int) -> FastMCP:
    mcp = FastMCP("general_memory_module")
    mcp.settings.port = port

    @mcp.tool(
        description=(
            "Recall what you remember across ALL of your memory (entities, chat, "
            "observations, narratives, jobs, messages) by meaning. Use this when "
            "you need context about a person, topic, past decision, or anything you "
            "may have learned before. Returns the most relevant memories, ranked."
        )
    )
    async def remember(agent_id: str, query: str, limit: int = 15) -> dict:
        # Routes through the AgentDataStore seam: DirectStore locally (same
        # MemoryCoordinator call as before), HttpStore in cloud (no db creds
        # in mcp). Both return the identical dict — see module/data_access.
        from xyz_agent_context.module.data_access import get_agent_data_store

        return await get_agent_data_store().remember(agent_id, query, limit)

    @mcp.tool(
        description=(
            "Search your memory for an EXACT string or regex pattern (like grep). "
            "Use this when you need a precise token you saw before — an id, URL, "
            "order number, file path, exact name spelling — that semantic recall "
            "might miss. Set regex=true to use a regular expression."
        )
    )
    async def grep_memory(agent_id: str, pattern: str, regex: bool = False, limit: int = 30) -> dict:
        try:
            db = await XYZBaseModule.get_mcp_db_client()
            coord = MemoryCoordinator(MemoryEngine(db, agent_id))
            hits = await coord.grep_memory(pattern, regex=regex, limit=limit)
            return {"success": True, "pattern": pattern, "matches": format_memory_hits(hits)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.grep_memory] failed: {e}")
            return {"success": False, "error": str(e), "matches": []}

    @mcp.tool(
        description=(
            "Explicitly write ONE durable fact into your long-term General Memory. "
            "Use when you learn something worth keeping that the automatic per-turn "
            "extraction would miss — e.g. importing a fact from another agent, or a "
            "user preference they asked you to remember verbatim. `content` is the "
            "fact in natural language; `source` optionally notes where it came from "
            "(e.g. 'imported from MEMORY.md'). Facts are deduplicated by meaning."
        )
    )
    async def memory_retain(agent_id: str, content: str, source: str = "") -> dict:
        # Routes through the AgentDataStore seam (see remember above).
        from xyz_agent_context.module.data_access import get_agent_data_store

        return await get_agent_data_store().memory_retain(agent_id, content, source)

    logger.info(
        f"GeneralMemory MCP: remember + grep_memory + memory_retain registered on port {port}"
    )
    return mcp
