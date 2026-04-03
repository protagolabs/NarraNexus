"""
@file_name: message_bus_module.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBusModule - Agent-to-agent communication via MessageBus

Replaces MatrixModule with a protocol-agnostic message bus. Provides MCP tools
for sending/receiving messages, managing channels, and discovering agents.

Instance level: Agent-level (one per Agent, is_public=True).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)


# MCP server port for MessageBus tools
MESSAGE_BUS_MCP_PORT = 7820


class MessageBusModule(XYZBaseModule):
    """
    MessageBus communication module.

    Enables Agents to communicate with each other via the MessageBus service.
    Provides MCP tools for messaging, channel management, and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="MessageBusModule",
            priority=5,
            enabled=True,
            description=(
                "Agent-to-agent communication via message bus. "
                "Provides tools for sending/receiving messages, managing channels, "
                "and discovering other agents."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="message_bus_module",
            server_url=f"http://localhost:{MESSAGE_BUS_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            from fastmcp import FastMCP

            mcp = FastMCP("MessageBusModule MCP")
            mcp.settings.port = MESSAGE_BUS_MCP_PORT

            from ._message_bus_mcp_tools import register_message_bus_mcp_tools
            register_message_bus_mcp_tools(mcp, get_message_bus_fn=_get_default_bus)

            logger.info(f"MessageBusModule MCP server created on port {MESSAGE_BUS_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.error(f"Failed to create MessageBusModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        unread_info = ctx_data.extra_data.get("bus_unread_messages", [])
        channels = ctx_data.extra_data.get("bus_channels", [])
        known_agents = ctx_data.extra_data.get("known_agents", [])

        instructions = """
#### MessageBus Communication

MessageBus is your **inter-agent messaging channel**. Use it to collaborate with
other Agents, exchange information, and coordinate tasks.

Available tools (prefix: bus_*):
- `bus_send_to_agent`: Send a direct message to another agent by agent_id
- `bus_send_message`: Send a message to a channel
- `bus_create_channel`: Create a new channel and invite members
- `bus_search_agents`: Search for agents in the registry
- `bus_get_unread`: Get your unread messages
- `bus_register_agent`: Register yourself in the agent discovery registry

##### When to Use MessageBus
- You need to **contact another Agent** (use `bus_send_to_agent` with their agent_id)
- You want to **discover agents** by capability or description (use `bus_search_agents`)
- Your owner asks you to **send a message** to another agent
"""

        # Show known agents (from DB + registry)
        if known_agents:
            instructions += "\n##### Known Agents (you can contact them)\n"
            for agent in known_agents[:30]:
                agent_id = agent.get("agent_id", "unknown")
                name = agent.get("agent_name", agent.get("name", agent_id))
                desc = agent.get("agent_description", agent.get("description", ""))
                instructions += f"- **{name}** (agent_id: `{agent_id}`)"
                if desc:
                    instructions += f" — {desc[:80]}"
                instructions += "\n"
            instructions += "\nTo message an agent: `bus_send_to_agent(to_agent_id='<agent_id>', content='...')`\n"

        # Show current channels
        if channels:
            instructions += "\n##### Your Channels\n"
            for ch in channels[:20]:
                ch_name = ch.get("name", ch.get("channel_id", "?"))
                ch_id = ch.get("channel_id", "?")
                instructions += f"- {ch_name} (`{ch_id}`)\n"

        # Show unread messages
        if unread_info:
            instructions += "\n##### Unread Messages\n"
            for msg in unread_info[:10]:
                instructions += (
                    f"- [{msg.get('from_agent', 'unknown')}] "
                    f"in channel {msg.get('channel_id', '?')}: "
                    f"{msg.get('content', '')[:80]}\n"
                )

        return instructions

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject MessageBus context into agent data.

        1. Auto-register current agent in bus_agent_registry
        2. Fetch all known agents (from agents DB table + bus_agent_registry)
        3. Fetch unread messages
        4. Fetch channel list
        """
        try:
            bus = _get_default_bus()
            if bus is None:
                return ctx_data

            # --- 1. Auto-register this agent in bus_agent_registry ---
            try:
                db = await _get_shared_db()
                if db:
                    # Get agent info from agents table
                    agent_row = await db.get_one("agents", {"agent_id": self.agent_id})
                    if agent_row:
                        owner = agent_row.get("created_by", "")
                        name = agent_row.get("agent_name", "")
                        desc = agent_row.get("agent_description", "")
                        is_public = agent_row.get("is_public", 0)
                        await bus.register_agent(
                            agent_id=self.agent_id,
                            owner_user_id=owner,
                            capabilities=[],
                            description=f"{name}: {desc}" if desc else name,
                            visibility="public" if is_public else "private",
                        )
            except Exception as e:
                logger.debug(f"Failed to auto-register agent in bus: {e}")

            # --- 2. Fetch all known agents ---
            known_agents = []
            try:
                db = await _get_shared_db()
                if db:
                    # Get all agents from the agents table (most reliable source)
                    all_agents = await db.get("agents", {})
                    for a in all_agents:
                        if a.get("agent_id") != self.agent_id:  # Exclude self
                            known_agents.append({
                                "agent_id": a.get("agent_id"),
                                "agent_name": a.get("agent_name", ""),
                                "agent_description": a.get("agent_description", ""),
                                "is_public": a.get("is_public", 0),
                                "created_by": a.get("created_by", ""),
                            })
                if known_agents:
                    ctx_data.extra_data["known_agents"] = known_agents
            except Exception as e:
                logger.debug(f"Failed to fetch known agents: {e}")

            # --- 3. Fetch unread messages ---
            try:
                unread = await bus.get_unread(self.agent_id)
                if unread:
                    ctx_data.extra_data["bus_unread_messages"] = [
                        msg.model_dump() for msg in unread
                    ]
            except Exception as e:
                logger.debug(f"Failed to fetch unread messages: {e}")

            # --- 4. Fetch channels ---
            try:
                rows = await bus._db.execute(
                    "SELECT c.* FROM bus_channels c "
                    "JOIN bus_channel_members cm ON c.channel_id = cm.channel_id "
                    "WHERE cm.agent_id = ?",
                    (self.agent_id,),
                )
                if rows:
                    ctx_data.extra_data["bus_channels"] = [dict(r) for r in rows]
            except Exception as e:
                logger.debug(f"Failed to load bus channels: {e}")

        except Exception as e:
            logger.error(f"MessageBusModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        if params.working_source != WorkingSource.MESSAGE_BUS:
            return

        try:
            bus = _get_default_bus()
            if bus is None:
                return

            unread = await bus.get_unread(self.agent_id)
            if unread:
                msg_ids = [m.message_id for m in unread]
                await bus.mark_read(self.agent_id, msg_ids)
                logger.info(
                    f"MessageBusModule: marked {len(msg_ids)} messages as read "
                    f"for agent {self.agent_id}"
                )
        except Exception as e:
            logger.error(f"MessageBusModule hook_after_event_execution failed: {e}")


# =============================================================================
# Module-level helpers
# =============================================================================

_bus_instance = None


def _get_default_bus():
    """Get or create the default LocalMessageBus using the shared DB backend."""
    global _bus_instance
    if _bus_instance is not None:
        return _bus_instance

    try:
        import asyncio
        from xyz_agent_context.message_bus import LocalMessageBus
        from xyz_agent_context.utils.db_factory import get_db_client

        # Get the shared DB client's backend
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context — can't await here
            # Use a sync fallback: try to get the existing shared client
            import xyz_agent_context.utils.db_factory as factory
            if hasattr(factory, '_shared_async_client') and factory._shared_async_client:
                backend = factory._shared_async_client._backend
                if backend:
                    _bus_instance = LocalMessageBus(backend=backend)
                    return _bus_instance

        # Fallback: create with a new backend from settings
        from xyz_agent_context.settings import settings
        url = getattr(settings, 'database_url', '') or ''
        if url.startswith('sqlite'):
            from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
            from xyz_agent_context.utils.db_factory import parse_sqlite_url
            db_path = parse_sqlite_url(url)
            backend = SQLiteBackend(db_path)
            # Can't await initialize() in sync context, but SQLiteBackend
            # might already be initialized via shared client
            _bus_instance = LocalMessageBus(backend=backend)
            return _bus_instance

        logger.warning("MessageBus: no SQLite URL configured, bus unavailable")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize default MessageBus: {e}")
        return None


async def _get_shared_db():
    """Get the shared AsyncDatabaseClient."""
    try:
        from xyz_agent_context.utils.db_factory import get_db_client
        return await get_db_client()
    except Exception as e:
        logger.debug(f"Failed to get shared DB client: {e}")
        return None
