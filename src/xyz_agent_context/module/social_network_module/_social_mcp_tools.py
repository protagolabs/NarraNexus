"""
@file_name: _social_mcp_tools.py
@author: NetMind.AI
@date: 2025-11-21
@description: SocialNetworkModule MCP Server tool definitions

Separates MCP tool registration logic from the SocialNetworkModule main class.

Tools:
- extract_entity_info: Extract and update entity information
- search_social_network: Search social network
- get_contact_info: Get contact information
- get_agent_social_stats: Get Agent social statistics
"""

from typing import Optional, Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.repository import InstanceRepository


def create_social_network_mcp_server(port: int, get_db_client_fn, module_class) -> FastMCP:
    """
    Create a SocialNetworkModule MCP Server instance

    Args:
        port: MCP Server port
        get_db_client_fn: Async function to get database connection
        module_class: SocialNetworkModule class reference (avoids circular imports)

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("social_network_module")
    mcp.settings.port = port

    async def _get_instance_and_module(agent_id: str):
        """Common helper: get db, instance_id and create temp module"""
        db = await get_db_client_fn()
        instance_repo = InstanceRepository(db)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )
        if not instances:
            return None, None, f"Error: No SocialNetworkModule instance found for agent_id={agent_id}"
        instance_id = instances[0].instance_id
        temp_module = module_class(agent_id=agent_id, database_client=db, instance_id=instance_id)
        return temp_module, instance_id, None

    @mcp.tool()
    async def extract_entity_info(
        agent_id: str,
        entity_id: str,
        updates: dict | str,
        update_mode: str = "merge"
    ) -> dict:
        """
        IMMEDIATELY call this when someone introduces themselves or shares personal/professional information.

        Extract and persistently store information about users, agents, or organizations.
        This is how you build and maintain your social network memory with structured tags and identity data.

        **When to call (DO NOT WAIT)**:
        - User introduces themselves (name, role, company, expertise)
        - Someone mentions another person/agent/organization
        - Contact info is shared (email, phone, website)
        - Any biographical or professional detail appears

        Args:
            agent_id: The ID of the agent who owns this social network
            entity_id: The user_id or agent_id of the person
            updates: Information to update (entity_name, identity_info, contact_info, tags)
                 DO NOT include entity_description - it's auto-managed by conversation summaries
            update_mode: How to update: 'merge' combines with existing info, 'replace' overwrites (default: 'merge')

        Returns:
            Operation result with success status and message

        Example 1 - Expert level (EXPLICITLY claims expertise):
            User: "你好，我是Alice，我是推荐系统专家"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_alice_123",
                updates={
                    "entity_type": "user",
                    "entity_name": "Alice",
                    "tags": ["expert:推荐系统", "researcher"]
                }
            )

        Example 2 - Familiar level (works with but doesn't claim expert):
            User: "我叫Bob，在Acme Corp做前端开发，主要用React"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_bob_456",
                updates={
                    "entity_type": "user",
                    "entity_name": "Bob",
                    "identity_info": {
                        "organization": "Acme Corp",
                        "position": "前端工程师",
                        "tech_stack": ["React"]
                    },
                    "tags": ["familiar:前端", "familiar:React", "engineer"]
                }
            )

        Example 3 - Interested level (learning or exploring):
            User: "我最近在学NLP，对大模型很感兴趣"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_carol_789",
                updates={
                    "entity_type": "user",
                    "entity_name": "Carol",
                    "tags": ["interested:NLP", "interested:大模型", "student"]
                }
            )


        Example 4 - Adding contact info:
            User: "我的邮箱是 alice@example.com"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_alice_123",
                updates={
                    "contact_info": {
                        "email": "alice@example.com"
                    }
                },
                update_mode="merge"  # Merges with existing info
            )
        """
        import json as _json

        # Process updates parameter
        if isinstance(updates, str):
            try:
                updates = _json.loads(updates)
            except _json.JSONDecodeError as e:
                return {
                    "success": False,
                    "message": f"Error: updates must be a valid JSON object, got string that failed to parse: {e}",
                    "entity_id": entity_id
                }

        if not isinstance(updates, dict):
            return {
                "success": False,
                "message": f"Error: updates must be a dictionary, got {type(updates).__name__}",
                "entity_id": entity_id
            }

        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        return await temp_module.extract_and_update_entity_info(
            entity_id=entity_id,
            instance_id=instance_id,
            updates=updates,
            update_mode=update_mode
        )

    @mcp.tool()
    async def search_social_network(
        agent_id: str,
        search_keyword: str,
        search_type: str = "auto",
        top_k: int = 5
    ) -> dict:
        """
        Search your social network for people. Supports exact lookup, tag search, and semantic search.

        Args:
            agent_id: The ID of the agent who owns this social network
            search_keyword: Can be:
                - Exact entity_id: "user_alice_123", "entity_bob_456"
                - Person's name: "Alice", "Bob"
                - Tag: "expert:推荐系统", "architect", "familiar:机器学习"
                - Natural language query (for semantic search): "谁最近表现出购买意向？"
            search_type: Type of search - 'auto' (recommended), 'exact_id', 'tags', 'semantic'
                - 'auto': Automatically detects if it's an entity_id or tag/name
                - 'exact_id': Force exact entity_id lookup
                - 'tags': Search by tags only
                - 'semantic': Natural language semantic search using embeddings
            top_k: Number of results to return (default: 5, ignored for exact_id)

        Returns:
            Search results with matching entities and their information (INCLUDING contact_info)
            For semantic search, results also include 'similarity_score' (0-1)

        Example 1 - Find specific person by entity_id:
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="user_alice_123",
                search_type="auto"
            )

        Example 2 - Find person by name:
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="Bob",
                search_type="auto"
            )

        Example 3 - Find experts by tag:
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="expert:推荐系统",
                search_type="tags",
                top_k=5
            )

        Example 4 - Semantic search (natural language):
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="谁最近表现出购买意向？",
                search_type="semantic",
                top_k=5
            )

        Note: Results include contact_info, so you usually don't need to call get_contact_info afterward.
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error, "results": []}

        return await temp_module.search_network(
            search_keyword=search_keyword,
            instance_id=instance_id,
            search_type=search_type,
            top_k=top_k
        )

    @mcp.tool()
    async def get_contact_info(agent_id: str, entity_id: str) -> dict:
        """
        Get contact information for reaching out to someone in your network.
        Use this when you need to know how to contact a specific person.

        Args:
            agent_id: The ID of the agent who owns this social network
            entity_id: The user_id or agent_id of the person

        Returns:
            Contact information including chat_channel, email, preferred_method, etc.
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        result = await temp_module.recall_entity_info(entity_id, instance_id)

        if result["success"]:
            entity = result["entity"]
            return {
                "success": True,
                "entity_id": entity_id,
                "entity_name": entity.get("entity_name"),
                "contact_info": entity.get("contact_info", {})
            }
        else:
            return {"success": False, "message": result["message"]}

    @mcp.tool()
    async def get_agent_social_stats(
        agent_id: str,
        sort_by: str = "recent",
        top_k: int = 5,
        filter_tags: str = ""
    ) -> dict:
        """
        View your social network from Agent's perspective - perfect for sales/outreach tracking!

        This tool lets you (the Agent's owner) ask questions like:
        - "Who did you contact recently?"
        - "Which customers engage with you most?"
        - "Show me your best relationships"

        Args:
            agent_id: The ID of the agent
            sort_by: How to sort results:
                - "recent": Most recently contacted (best for "who did you talk to lately?")
                - "frequent": Most interactions (best for "who engages most?")
                - "strong": Strongest relationships (best for "your best contacts?")
            top_k: Number of results to return (default: 5)
            filter_tags: Optional comma-separated tags to filter (e.g., "expert:前端,architect")

        Returns:
            Sorted list with FULL entity info including:
            - entity_name, entity_description ← Key! Shows conversation summary
            - interaction_count, last_interaction_time
            - tags, contact_info, relationship_strength

        Example 1 - Sales Agent reporting recent contacts:
            get_agent_social_stats(
                agent_id="sales_agent_001",
                sort_by="recent",
                top_k=5
            )

        Example 2 - Find most active customers:
            get_agent_social_stats(
                agent_id="sales_agent_001",
                sort_by="frequent",
                top_k=10
            )

        Example 3 - Check progress with frontend experts:
            get_agent_social_stats(
                agent_id="sales_agent_001",
                sort_by="recent",
                filter_tags="expert:前端"
            )
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error, "results": []}

        # Parse filter_tags
        filter_tags_list = None
        if filter_tags and filter_tags.strip():
            filter_tags_list = [tag.strip() for tag in filter_tags.split(",")]

        results = await temp_module._get_agent_stats(
            instance_id=instance_id,
            sort_by=sort_by,
            top_k=top_k,
            filter_tags=filter_tags_list
        )

        return {
            "success": True,
            "sort_by": sort_by,
            "count": len(results),
            "results": results
        }

    return mcp
