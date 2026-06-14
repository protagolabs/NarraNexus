"""
@file_name: module_service.py
@author: NetMind.AI
@date: 2025-12-22
@description: Module service protocol layer

This is the external interface of ModuleService; all concrete implementations are delegated to the _module_impl module.

Features:
1. load_modules() - Load modules
2. Execution path decision
3. Instance management
"""

from __future__ import annotations

from typing import Any, List, Optional, TYPE_CHECKING

from loguru import logger

from xyz_agent_context.schema import ModuleLoadResult

from ._module_impl import ModuleLoader

if TYPE_CHECKING:
    from xyz_agent_context.narrative import Narrative
    from xyz_agent_context.utils import DatabaseClient
    from xyz_agent_context.module import XYZBaseModule


class ModuleService:
    """
    Module Service - Main interface for AgentRuntime

    This is a protocol layer; all concrete implementations are delegated to the _module_impl module.

    Main features:
    1. load_modules() - Load modules and perform Instance decision
    2. LLM Instance intelligent decision (default mode)

    Usage example:
        >>> service = ModuleService(agent_id, user_id, db_client)
        >>> result = await service.load_modules(
        ...     narrative_list=narratives,
        ...     input_content="Help me look up Zhang San's contact info"
        ... )
    """

    # Default static module list
    DEFAULT_MODULE_LIST = [
        "MemoryModule",
        "AwarenessModule",
        "ChatModule",
        "BasicInfoModule",
        "SocialNetworkModule",
        "JobModule",
        "MessageBusModule",
        "LarkModule",
    ]

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        database_client: "DatabaseClient",
        policy: Optional[Any] = None,
    ):
        """
        Initialize ModuleService

        Args:
            agent_id: Agent ID
            user_id: User ID
            database_client: Database client
            policy: Optional RuntimePolicy from a runtime variant. When
                non-None, `policy.skipped_modules` filters MODULE_MAP at
                construction (those modules never instantiate), and the
                policy itself is passed into every created module's
                constructor so policy-aware modules can read it. None =
                main-runtime behaviour, no filtering.
        """
        self.agent_id = agent_id
        self.user_id = user_id
        self.database_client = database_client
        self._policy = policy

        # Get MODULE_MAP (lazy import to avoid circular references)
        from xyz_agent_context.module import MODULE_MAP
        # Filter out modules the policy forbids loading. The filtered map
        # is used by both the loader (decision-time module list) and
        # create_module (explicit instantiation guard).
        skipped = getattr(policy, "skipped_modules", frozenset()) if policy else frozenset()
        self._module_map = {
            name: cls for name, cls in MODULE_MAP.items() if name not in skipped
        }
        if skipped:
            logger.info(
                f"ModuleService: policy skipped_modules filtered out: "
                f"{sorted(skipped & set(MODULE_MAP.keys()))}"
            )

        # Implementation modules
        self._loader = ModuleLoader(
            agent_id=agent_id,
            user_id=user_id,
            database_client=database_client,
            module_map=self._module_map,
            policy=policy,
        )

        logger.info(f"ModuleService initialized (agent_id={agent_id})")

    async def load_modules(
        self,
        narrative_list: List["Narrative"],
        module_name_list: Optional[List[str]] = None,
        input_content: Optional[str] = None,
        use_instance_decision: bool = True,
        narrative_summary: str = "",
        markdown_history: str = "",
        awareness: str = "",
        working_source: Optional[str] = None,
    ) -> ModuleLoadResult:
        """
        Load Module instances and decide execution path

        Supports two modes:
        1. Instance decision mode (default): Uses LLM for intelligent Instance management
        2. Traditional mode: Uses module_name_list or default module list

        Args:
            narrative_list: Narrative list
            module_name_list: Specified module name list (traditional mode)
            input_content: User input content (required for Instance decision mode)
            use_instance_decision: Whether to use LLM Instance intelligent decision (default True)
            narrative_summary: Narrative summary
            markdown_history: History
            awareness: Agent self-awareness content
            working_source: Working source

        Returns:
            ModuleLoadResult
        """
        return await self._loader.load_modules(
            narrative_list=narrative_list,
            module_name_list=module_name_list,
            input_content=input_content,
            use_instance_decision=use_instance_decision,
            narrative_summary=narrative_summary,
            markdown_history=markdown_history,
            awareness=awareness,
            working_source=working_source,
        )

    def get_all_module_names(self) -> List[str]:
        """Get all available Module names"""
        return list(self._module_map.keys())

    def create_module(
        self,
        module_name: str,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ) -> Optional["XYZBaseModule"]:
        """
        Create a single Module instance

        Args:
            module_name: Module name
            instance_id: Instance ID
            instance_ids: Related Instance IDs

        Returns:
            Module instance, or None if the module does not exist
        """
        if module_name not in self._module_map:
            logger.warning(f"ModuleService: Unknown module name '{module_name}'")
            return None

        module_class = self._module_map[module_name]
        return module_class(
            self.agent_id,
            self.user_id,
            self.database_client,
            instance_id=instance_id,
            instance_ids=instance_ids or [],
            policy=self._policy,
        )
