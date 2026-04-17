"""
@file_name: _common_tools_mcp_tools.py
@author: Bin Liang
@date: 2026-04-17
@description: MCP server + tool definitions for CommonToolsModule

Tools exposed:
- web_search(queries, max_results_per_query): DuckDuckGo search, multi-query

Stateless — tools take plain arguments, no agent_id / user_id bookkeeping.
"""

from loguru import logger
from mcp.server.fastmcp import FastMCP


def create_common_tools_mcp_server(port: int) -> FastMCP:
    mcp = FastMCP("common_tools_module")
    mcp.settings.port = port

    @mcp.tool()
    async def web_search(
        queries: list[str],
        max_results_per_query: int = 5,
    ) -> str:
        """Search the web via DuckDuckGo and return the top hits.

        Accepts a **list** of queries and runs them in parallel — pass multiple
        queries when you want to cover different angles in a single round trip.

        Each entry in `queries` can be EITHER:
        - A natural-language question (e.g. "How does Python asyncio gather handle exceptions?")
        - A set of keywords (e.g. "python asyncio gather exception propagation")

        Use whichever form is more likely to match how the information is written
        on the web. For factual lookups, keywords often work better; for
        reasoning/"how/why" questions, full sentences often retrieve better pages.

        Args:
            queries: List of search queries. Empty strings are dropped.
                Recommended: 1–5 queries per call. DuckDuckGo will rate-limit
                aggressive fan-out.
            max_results_per_query: Max hits per query. Default 5, hard cap 10.

        Returns:
            Markdown-formatted results grouped by query. Each hit has title,
            URL, and a short snippet. If a query fails, the error is reported
            inline without breaking the other queries.
        """
        from xyz_agent_context.module.common_tools_module._common_tools_impl.web_search import (
            search_many,
            format_results,
        )

        try:
            bundles = await search_many(queries, max_results_per_query)
        except Exception as e:  # defensive — search_many already swallows per-query errors
            logger.error(f"CommonToolsMCP: web_search top-level crash: {e}")
            return f"web_search failed: {e}"

        logger.info(
            f"CommonToolsMCP: web_search returned {sum(len(b['results']) for b in bundles)} hits "
            f"across {len(bundles)} queries"
        )
        return format_results(bundles)

    return mcp
