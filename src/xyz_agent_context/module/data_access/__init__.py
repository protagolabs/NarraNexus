"""Agent data-access abstraction for MCP tools (blueprint P0).

MCP tools depend on the AgentDataStore protocol, not on repositories/db, so the
transport can be swapped by the composition root: DirectStore (local — direct
repository access) or HttpStore (cloud — backend API, no DB creds in mcp).
"""
from xyz_agent_context.module.data_access.factory import get_agent_data_store
from xyz_agent_context.module.data_access.store import (
    AgentDataStore,
    DirectStore,
    HttpStore,
)

__all__ = ["AgentDataStore", "DirectStore", "HttpStore", "get_agent_data_store"]
