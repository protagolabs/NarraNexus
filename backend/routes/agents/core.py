"""
@file_name: core.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent route aggregator

Aggregates domain-specific sub-routers under the /api/agents prefix:
- Awareness (self-awareness)
- Social Network (entity management)
- Chat History (narratives & events)
- Files (workspace file management)
- MCPs (MCP URL management)
- RAG (RAG file management)
"""

from fastapi import APIRouter

from backend.routes.agents.awareness import router as awareness_router
from backend.routes.agents.social_network import router as social_network_router
from backend.routes.agents.chat_history import router as chat_history_router
from backend.routes.agents.files import router as files_router
from backend.routes.agents.attachments import router as attachments_router
from backend.routes.agents.mcps import router as mcps_router
from backend.routes.agents.cost import router as cost_router
from backend.routes.agents.bus_failures import router as bus_failures_router
from backend.routes.agents.llm_config import router as llm_config_router
from backend.routes.agents.circuit_breaker import router as circuit_breaker_router
from backend.routes.agents.general_memory import router as general_memory_router
from backend.routes.agents.narrative import router as narrative_router
from backend.routes.agents.jobs import router as jobs_router
from backend.routes.agents.profile import router as profile_router
from backend.routes.agents.channel_credentials import router as channel_credentials_router


router = APIRouter()

router.include_router(awareness_router)
router.include_router(social_network_router)
router.include_router(chat_history_router)
router.include_router(files_router)
router.include_router(attachments_router)
router.include_router(mcps_router)
router.include_router(cost_router)
router.include_router(bus_failures_router)
router.include_router(llm_config_router)
router.include_router(general_memory_router)
router.include_router(narrative_router)
router.include_router(jobs_router)
router.include_router(profile_router)
router.include_router(circuit_breaker_router)
router.include_router(channel_credentials_router)
