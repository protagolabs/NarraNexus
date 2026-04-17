"""
@file_name: common_tools_module.py
@author: Bin Liang
@date: 2026-04-17
@description: CommonToolsModule — generic utility tools for every agent

This module covers tools that are useful to every agent regardless of domain:
- `web_search`: DuckDuckGo search (replaces Anthropic's built-in web_search for
  non-Anthropic providers like NetMind that do not ship one)

Design choices:
- module_type="capability": always loaded, no instance record, no decision LLM
- Stateless MCP tools: the tools accept plain arguments; no per-agent state
- Room to grow: extra utilities (fetch_url, read_pdf, ...) live under the same
  MCP server to keep the tool-count moderate
"""

from typing import Any, List, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)
from xyz_agent_context.utils import DatabaseClient


COMMON_TOOLS_INSTRUCTIONS = """\
#### Generic Web Search

You have access to `web_search(queries: list[str], max_results_per_query: int = 5)`.
Use it whenever you need up-to-date information that is not in your context:

- Each entry in `queries` can be a **natural-language question**
  (e.g. "What is the latest iPhone 17 release date?") **or a keyword string**
  (e.g. "python asyncio gather exceptions"). Pick whichever matches how the
  information is likely written on the web.
- Pass **multiple queries at once** when you want to cover different angles
  (e.g. official docs + user discussion + recent news). They run in parallel.
- Results come back as title + URL + snippet grouped by query. If you need the
  full page, follow up with a fetch tool; do not assume the snippet is the
  whole answer.
- The search engine is DuckDuckGo — no API key required, but it is
  rate-limited, so avoid hammering it with dozens of queries in a row.
"""


class CommonToolsModule(XYZBaseModule):
    """Always-on capability module exposing generic tools (web_search, ...)."""

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = 7807
        self.instructions = COMMON_TOOLS_INSTRUCTIONS

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="CommonToolsModule",
            priority=50,
            enabled=True,
            description="Generic utility tools available to every agent (web_search, ...)",
            module_type="capability",
        )

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        return ctx_data

    async def get_instructions(self, ctx_data: ContextData) -> str:
        return self.instructions

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="common_tools_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        from xyz_agent_context.module.common_tools_module._common_tools_mcp_tools import (
            create_common_tools_mcp_server,
        )
        logger.debug(f"CommonToolsModule: creating MCP server on port {self.port}")
        return create_common_tools_mcp_server(self.port)
