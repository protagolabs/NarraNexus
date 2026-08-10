"""
@file_name: social_network.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Social Network routes

Read endpoints:
- GET /{agent_id}/social-network - Get all social entities for an Agent
- GET /{agent_id}/social-network/{user_id} - Get social network info for a specific user
- GET /{agent_id}/social-network/search - Search social entities (keyword/semantic)

Write endpoints (2026-08-10, PR-2 · MCP data-access seam backend half):
- POST /{agent_id}/social-network/extract - extract/update entity info
- POST /{agent_id}/social-network/merge - merge two entity records
- POST /{agent_id}/social-network/delete-entity - permanently delete an entity
- POST /{agent_id}/social-network/create-agent - create a new agent owned by the caller

Every write endpoint mirrors the corresponding tool in
`xyz_agent_context/module/social_network_module/_social_mcp_tools.py`
(same repository/module calls, same data semantics) so an HTTP caller and an
agent's own MCP tool call produce identical results — this route is the
non-agent-triggered path to the same data.
"""

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import (
    SocialNetworkRepository,
    InstanceRepository,
    AgentRepository,
)
from xyz_agent_context.bootstrap.provision import provision_new_agent
from xyz_agent_context.module.social_network_module import SocialNetworkModule
from xyz_agent_context.schema import (
    SocialNetworkEntityInfo,
    SocialNetworkResponse,
    SocialNetworkListResponse,
    SocialNetworkSearchResponse,
)

# Ownership gate (backend/routes/_ownership.py): agent_id is attacker-
# controlled path input — without the owner check any caller could extract/
# merge/delete another user's social-network entities, or spin up agents
# under someone else's account. Local mode (no JWT identity) does not
# enforce; see the helper's security-posture docstring before assuming auth.
from backend.routes._ownership import assert_owned


router = APIRouter()


def _parse_json(value: Any, default: Any) -> Any:
    """
    Parse JSON field

    Handles JSON strings stored in the database, converting them to Python objects.
    Supports dict and list JSON types, also handles double-encoding cases.
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            # Handle double-encoding
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except json.JSONDecodeError:
                    pass
            return parsed
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed for value: {value[:100]}... Error: {e}")
            return default
    logger.warning(f"Unexpected type for JSON field: {type(value)}, value: {value}")
    return default


@router.get("/{agent_id}/social-network/search", response_model=SocialNetworkSearchResponse)
async def search_social_network_entities(
    agent_id: str,
    query: str = Query(..., description="Search query"),
    search_type: str = Query("semantic", description="Search type: 'keyword' or 'semantic'"),
    limit: int = Query(10, description="Maximum number of results")
):
    """
    Search social entities by keyword (entity_name, entity_description, tags).
    `search_type` is accepted for API compatibility but always resolves to
    keyword search — embedding/semantic search is retired.

    NOTE: This route MUST be registered before /{user_id} to avoid path shadowing.
    """
    logger.info(f"Searching social network entities: agent={agent_id}, query='{query}', type={search_type}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkSearchResponse(
                success=True, entities=[], count=0, search_type=search_type
            )

        instance_id = instances[0].instance_id
        social_repo = SocialNetworkRepository(db_client)

        # Embeddings retired — entity search is BM25-style keyword search only.
        results = await social_repo.keyword_search(
            instance_id=instance_id,
            keyword=query,
            limit=limit
        )
        entity_list = []
        for entity in results:
            entity_info = SocialNetworkEntityInfo(
                entity_id=entity.entity_id,
                entity_name=entity.entity_name,
                aliases=entity.aliases or [],
                entity_description=entity.entity_description,
                entity_type=entity.entity_type,
                familiarity=entity.familiarity or "known_of",
                identity_info=entity.identity_info or {},
                contact_info=entity.contact_info or {},
                tags=entity.keywords or [],
                keywords=entity.keywords or [],
                relationship_strength=entity.relationship_strength or 0.0,
                interaction_count=entity.interaction_count or 0,
                last_interaction_time=format_for_api(entity.last_interaction_time),
                persona=entity.persona,
                related_job_ids=entity.related_job_ids or [],
                expertise_domains=entity.expertise_domains or [],
            )
            entity_list.append(entity_info)

        return SocialNetworkSearchResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list),
            search_type=search_type
        )

    except Exception as e:
        logger.exception(f"Error searching social network entities: {e}")
        return SocialNetworkSearchResponse(
            success=False, error=str(e), search_type=search_type
        )


def _entity_to_info(e) -> SocialNetworkEntityInfo:
    """Map a SocialNetworkEntity (from the unified-memory-backed repo) to the
    API's SocialNetworkEntityInfo. `tags` and `keywords` are the same list."""
    return SocialNetworkEntityInfo(
        entity_id=e.entity_id,
        entity_name=e.entity_name,
        aliases=e.aliases,
        entity_description=e.entity_description,
        entity_type=e.entity_type,
        familiarity=e.familiarity or "known_of",
        identity_info=e.identity_info,
        contact_info=e.contact_info,
        tags=e.keywords,
        keywords=e.keywords,
        relationship_strength=e.relationship_strength,
        interaction_count=e.interaction_count,
        last_interaction_time=format_for_api(e.last_interaction_time),
        persona=e.persona,
        related_job_ids=e.related_job_ids,
        expertise_domains=e.expertise_domains,
    )


@router.get("/{agent_id}/social-network/{user_id}", response_model=SocialNetworkResponse)
async def get_user_social_network_info(agent_id: str, user_id: str):
    """
    Get a user's information in the Agent's social network

    Queries data from instance_social_entities table (via SocialNetworkModule's instance_id).
    """
    logger.info(f"Getting social network info for user: {user_id}, agent: {agent_id}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkResponse(
                success=False,
                error=f"No SocialNetworkModule instance found for agent: {agent_id}"
            )

        instance_id = instances[0].instance_id

        entity = await SocialNetworkRepository(db_client).get_entity(user_id, instance_id)
        if entity:
            return SocialNetworkResponse(success=True, entity=_entity_to_info(entity))
        else:
            return SocialNetworkResponse(
                success=False,
                error=f"No social network info found for user: {user_id}"
            )

    except Exception as e:
        logger.exception(f"Error getting social network info: {e}")
        return SocialNetworkResponse(success=False, error=str(e))


@router.get("/{agent_id}/social-network", response_model=SocialNetworkListResponse)
async def get_all_social_network_entities(agent_id: str):
    """
    Get all social entities for an Agent

    Queries data from instance_social_entities table (via SocialNetworkModule's instance_id).
    """
    logger.debug(f"Getting all social network entities for agent: {agent_id}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkListResponse(success=True, entities=[], count=0)

        instance_id = instances[0].instance_id

        entities = await SocialNetworkRepository(db_client).get_all_entities(instance_id, limit=1000)
        entity_list = [_entity_to_info(e) for e in entities]

        return SocialNetworkListResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list)
        )

    except Exception as e:
        logger.exception(f"Error getting social network entities: {e}")
        return SocialNetworkListResponse(success=False, error=str(e))


# ============================================================================= Write endpoints


class ExtractEntityBody(BaseModel):
    """Body for POST .../extract — mirrors the `extract_entity_info` MCP
    tool's params minus `agent_id` (taken from the path instead)."""
    entity_id: str = Field(min_length=1, max_length=128)
    updates: dict[str, Any]
    update_mode: str = "merge"


class MergeEntitiesBody(BaseModel):
    """Body for POST .../merge — mirrors the `merge_entities` MCP tool's params."""
    source_entity_id: str = Field(min_length=1, max_length=128)
    target_entity_id: str = Field(min_length=1, max_length=128)
    keep_target_name: bool = True


class DeleteEntityBody(BaseModel):
    """Body for POST .../delete-entity — mirrors the `delete_entity` MCP tool's params."""
    entity_id: str = Field(min_length=1, max_length=128)


class CreateAgentBody(BaseModel):
    """Body for POST .../create-agent — mirrors the `create_agent` MCP tool's
    params minus `agent_id` (the creator, taken from the path instead)."""
    agent_name: str = Field(min_length=1, max_length=128)
    awareness: str = Field(default="", max_length=65536)
    agent_description: str = Field(default="", max_length=2000)


async def _resolve_social_instance_id(db_client, agent_id: str) -> tuple[str | None, str | None]:
    """Resolve the agent's SocialNetworkModule instance id.

    Same repository call as the instance-lookup half of
    `_get_instance_and_module` in `_social_mcp_tools.py`. The "no instance"
    error text intentionally matches this file's existing GET endpoints
    ("... for agent: {agent_id}") rather than the MCP tool's phrasing
    ("... for agent_id={agent_id}") — for an HTTP route family, staying
    consistent with the sibling GET endpoints in this file wins over
    matching the tool's wording verbatim.
    """
    instance_repo = InstanceRepository(db_client)
    instances = await instance_repo.get_by_agent(agent_id=agent_id, module_class="SocialNetworkModule")
    if not instances:
        return None, f"No SocialNetworkModule instance found for agent: {agent_id}"
    return instances[0].instance_id, None


def _normalize_write_result(result: dict) -> dict:
    """Map the wrapped module/tool 'message' failure key onto this route
    family's 'error' key (the shape the GET endpoints above use), leaving
    success payloads untouched. The MCP tools this file wraps always report
    failure via 'message', not 'error'."""
    if isinstance(result, dict) and result.get("success") is False and "error" not in result and "message" in result:
        result = dict(result)
        result["error"] = result.pop("message")
    return result


@router.post("/{agent_id}/social-network/extract")
async def extract_entity_info(agent_id: str, body: ExtractEntityBody, request: Request) -> dict:
    """
    Extract/update entity info — same operation as the `extract_entity_info`
    MCP tool, reached over HTTP instead of an agent tool call. Delegates to
    `SocialNetworkModule.extract_and_update_entity_info` so merge/replace
    semantics (tag dedup capped at 10, identity/contact deep-merge,
    entity_description protected from direct overwrite) stay identical to
    the agent-facing path.
    """
    await assert_owned(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "error": error}

        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        result = await temp_module.extract_and_update_entity_info(
            entity_id=body.entity_id,
            instance_id=instance_id,
            updates=dict(body.updates),
            update_mode=body.update_mode,
        )
        return _normalize_write_result(result)

    except Exception as e:
        logger.exception(f"Error extracting entity info: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/social-network/merge")
async def merge_entities(agent_id: str, body: MergeEntitiesBody, request: Request) -> dict:
    """
    Merge two entity records — same operation as the `merge_entities` MCP
    tool: source is folded into target (tags/identity/contact_info/
    related_job_ids unioned, descriptions appended, interaction counts
    summed, newest last_interaction_time kept) then the source is deleted.

    Delegates to `SocialNetworkModule.merge_entities` (pre-open review #2:
    this used to be a byte-for-byte copy of the MCP tool's inline closure
    body; both the tool and this route now call the same module method so
    the merge semantics can't drift between the two entry points).
    """
    await assert_owned(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "error": error}

        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        result = await temp_module.merge_entities(
            source_entity_id=body.source_entity_id,
            target_entity_id=body.target_entity_id,
            instance_id=instance_id,
            keep_target_name=body.keep_target_name,
        )
        return _normalize_write_result(result)

    except Exception as e:
        logger.exception(f"Error merging entities: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/social-network/delete-entity")
async def delete_entity(agent_id: str, body: DeleteEntityBody, request: Request) -> dict:
    """
    Permanently delete a social network entity — same operation as the
    `delete_entity` MCP tool. POST (not HTTP DELETE) so the target entity_id
    travels in the body, consistent with this route family's other write
    endpoints.

    Delegates to `SocialNetworkModule.delete_entity` (pre-open review #2 —
    see the `merge` endpoint above for the same rationale).
    """
    await assert_owned(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "error": error}

        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        result = await temp_module.delete_entity(entity_id=body.entity_id, instance_id=instance_id)
        return _normalize_write_result(result)

    except Exception as e:
        logger.exception(f"Error deleting entity: {e}")
        return {"success": False, "error": str(e)}


@router.post("/{agent_id}/social-network/create-agent")
async def create_agent(agent_id: str, body: CreateAgentBody, request: Request) -> dict:
    """
    Create a new agent owned by the caller — same operation as the
    `create_agent` MCP tool, via the shared `provision_new_agent` seam: agent
    row + default module instances + peer-discovery registration + bootstrap
    profile + default-skill install + awareness seed. `agent_id` in the path
    is the CREATOR (must be owned by the caller); the new agent inherits the
    creator's `created_by`, i.e. it lands in the same user's account, not the
    creator agent's. Any non-fatal provisioning warning (a half-provisioned
    agent) is surfaced in the response so ops can see it (incident lesson #5).
    """
    await assert_owned(request, agent_id)

    try:
        db_client = await get_db_client()
        agent_repo = AgentRepository(db_client)
        caller = await agent_repo.get_agent(agent_id)
        if not caller or not caller.created_by:
            return {"success": False, "error": "Cannot determine your owner (created_by). Aborting."}

        owner_user_id = caller.created_by
        new_agent_id = f"agent_{uuid4().hex[:12]}"

        # Canonical provisioning seam (pre-open review #3): agent row +
        # default module instances + peer-discovery registration + bootstrap
        # profile + default-skill install + awareness seed, all in one call.
        # This used to be a hand-maintained copy of auth.py's create_agent
        # sequence; now both this route and the MCP `create_agent` tool call
        # the same `provision_new_agent`, so they can't drift from each
        # other again (and this route no longer risks missing a step like
        # default-skill install the way the MCP tool's old copy did).
        result = await provision_new_agent(
            db_client,
            agent_id=new_agent_id,
            user_id=owner_user_id,
            agent_name=body.agent_name,
            agent_description=body.agent_description or f"Agent created by {caller.agent_name or agent_id}",
            awareness=body.awareness,
        )
        logger.info(f"Created agent {new_agent_id} ('{body.agent_name}') for owner {owner_user_id}")

        response = {
            "success": True,
            "message": (
                f"Agent '{body.agent_name}' created successfully (ID: {new_agent_id}). "
                f"The user can now switch to this agent in the frontend. "
                f"If further configuration is needed (skills, jobs, etc.), "
                f"tell the user to interact with the new agent directly."
            ),
            "new_agent_id": new_agent_id,
            "agent_name": body.agent_name,
        }
        # A half-provisioned agent is an ops-relevant fact (incident lesson
        # #5): surface any non-fatal provisioning warning instead of dropping
        # the seam's collected list.
        if result.warnings:
            response["warnings"] = result.warnings
        return response

    except Exception as e:
        logger.exception(f"Error creating agent: {e}")
        return {"success": False, "error": str(e)}

