"""
@file_name: _chat_mcp_tools.py
@author: Bin Liang
@date: 2026-03-06
@description: ChatModule MCP Server tool definitions

Separates MCP tool registration logic from ChatModule main class,
keeping the module focused on Hook lifecycle and memory management.

Tools:
- send_message_to_user_directly: Agent speaks to user (the ONLY way to deliver messages)
- get_chat_history: Get chat history for a Chat Instance
"""

from mcp.server.fastmcp import FastMCP

def create_chat_mcp_server(port: int) -> FastMCP:
    """
    Create a ChatModule MCP Server instance

    Args:
        port: MCP Server port

    Returns:
        FastMCP instance with all tools configured

    (No db-client function is needed anymore: get_chat_history routes through the
    AgentDataStore seam, which resolves its own db, and send_message_to_user_directly
    never touches the db.)
    """
    mcp = FastMCP("chat_module")
    mcp.settings.port = port

    @mcp.tool()
    async def get_chat_history(
        agent_id: str,
        instance_id: str,
        limit: int = 20
    ) -> dict:
        """
        Get chat history for a specified Chat Instance.

        Each user has an independent Chat Instance within a Narrative, used to store that user's conversation history with the Agent.
        When a sales manager asks about interactions with a specific customer, this tool can be used to get that customer's complete chat history.
        Returned messages are sorted chronologically and contain both user and Agent conversation content.

        Args:
            agent_id: Your agent ID (the owner of the Chat Instance). The instance
                must belong to you — another agent's instance returns empty history.
            instance_id: Chat Instance ID (format: chat_xxxxxxxx), used to locate a specific user's conversation
            limit: Maximum number of messages to return, default 20. Set to -1 to return all

        Returns:
            dict: Dictionary containing chat history, format:
            {
                "success": True/False,
                "instance_id": "chat_xxx",
                "total_messages": 10,
                "messages": [
                    {"role": "user", "content": "...", "timestamp": "..."},
                    {"role": "assistant", "content": "...", "timestamp": "..."},
                    ...
                ]
            }

        Example:
            # Get conversation history with customer Alice
            # Assuming Alice's Chat Instance ID is "chat_abc12345"
            get_chat_history(
                agent_id="agent_123",
                instance_id="chat_abc12345",
                limit=10
            )
        """
        # Route through the AgentDataStore seam: DirectStore (local, unchanged
        # DB access) or HttpStore (cloud, backend API — no db creds in mcp),
        # chosen by NARRANEXUS_BACKEND_URL. The de-rawed, instance-scoped body
        # is the shared fetch_chat_history (_chat_reads), so this path and the
        # backend twin route stay byte-identical.
        from xyz_agent_context.module.data_access import get_agent_data_store
        return await get_agent_data_store().get_chat_history(agent_id, instance_id, limit)

    @mcp.tool()
    async def send_message_to_user_directly(agent_id: str, user_id: str, content: str) -> dict:
        """
        Speak to the user - This is the ONLY way to deliver your response to the user.

        **CRITICAL**: Think of this as "opening your mouth to speak". All your internal reasoning,
        tool calls, and agent loop outputs are like thoughts in your mind - completely invisible
        to the user. The user ONLY sees what you say through this tool.

        Analogy: Imagine you and the user are face-to-face:
        - Your LLM reasoning = thinking in your head (user cannot hear)
        - Your tool calls = actions you take silently (user cannot see)
        - Calling this tool = opening your mouth to speak (user CAN hear)

        Without calling this tool, your response stays in your head - the user receives NOTHING!

        Args:
            agent_id: Your agent ID (the speaker).
            user_id: The user ID you are speaking to (the listener).
            content: What you want to say to the user. This is the actual message
                     the user will see. Make it clear, helpful, and appropriate.
                     Which is in markdown format.

        Returns:
            A confirmation dict indicating the response was delivered successfully.

        Example:
            # After thinking and gathering information, speak to the user:
            send_message_to_user_directly(
                agent_id="agent_123",
                user_id="user_456",
                content="Based on my analysis, here are the results you requested..."
            )
        """
        return {
            "success": True,
            "message": "Response delivered to user successfully",
            "user_id": user_id,
            "agent_id": agent_id,
            "content": content
        }

    return mcp
