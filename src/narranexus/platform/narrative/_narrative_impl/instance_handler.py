"""
Instance management implementation

@file_name: instance_handler.py
@author: NetMind.AI
@date: 2025-12-22
@description: ModuleInstance dependency management and state transitions

Refactoring notes (2025-12-24):
- Instance status changes are written to the module_instances table
- Instance association changes are written to the instance_narrative_links table
- No longer operates on the narrative.active_instances JSON field
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING

from loguru import logger

from ..models import Narrative
from .crud import NarrativeCRUD

if TYPE_CHECKING:
    from narranexus.platform.schema.module_schema import ModuleInstance, InstanceStatus
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


class InstanceHandler:
    """
    Instance Manager

    Responsibilities:
    - Handle Instance completion events
    - Check dependencies
    - State transitions (BLOCKED -> ACTIVE)

    Refactoring notes:
    - Instance status changes are written to the module_instances table
    - Instance association changes are written to the instance_narrative_links table
    """

    def __init__(self, agent_id: str):
        """
        Initialize Instance Manager

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._db_client: Optional["AsyncDatabaseClient"] = None

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)
        self._db_client = db_client

    async def _get_db_client(self) -> "AsyncDatabaseClient":
        """Get the database client"""
        if self._db_client is None:
            from narranexus.platform.utils.db.db_factory import get_db_client
            self._db_client = await get_db_client()
        return self._db_client

    async def handle_completion(
        self,
        narrative_id: str,
        instance_id: str,
        new_status: "InstanceStatus",
        narrative: Optional[Narrative] = None,
        save_to_db: bool = True
    ) -> List[str]:
        """
        Handle Instance completion event

        Refactored workflow:
        1. Update instance status in the module_instances table
        2. Update instance_narrative_links table (mark as history)
        3. Check dependencies of other BLOCKED instances
        4. If dependencies are satisfied, transition BLOCKED -> ACTIVE

        Args:
            narrative_id: Narrative ID
            instance_id: Completed instance ID
            new_status: New status
            narrative: Narrative object (optional, for runtime cache update)
            save_to_db: Whether to save (deprecated, always saves to database)

        Returns:
            List of newly activated instance_ids
        """
        from narranexus.platform.schema.module_schema import InstanceStatus
        from narranexus.platform.repository import InstanceRepository, InstanceNarrativeLinkRepository
        from narranexus.platform.schema.instance_schema import LinkType

        logger.info(f"Handling instance completion: {instance_id} → {new_status.value}")

        db_client = await self._get_db_client()
        instance_repo = InstanceRepository(db_client)
        link_repo = InstanceNarrativeLinkRepository(db_client)

        # 1. Get instance information
        db_instance = await instance_repo.get_by_instance_id(instance_id)
        if not db_instance:
            logger.warning(f"Instance {instance_id} not found in database")
            return []

        # 2. Update instance status (write to module_instances table)
        now = datetime.now(timezone.utc)
        await instance_repo.update_status(
            instance_id=instance_id,
            status=new_status,
            completed_at=now if new_status in [InstanceStatus.COMPLETED, InstanceStatus.FAILED] else None
        )
        logger.info(f"Updated instance status: {instance_id} → {new_status.value}")

        # 3. Update association status (write to instance_narrative_links table)
        # Mark the association as history
        await link_repo.unlink(instance_id, narrative_id, to_history=True)
        logger.info(f"Unlinked instance from narrative: {instance_id} ↔ {narrative_id}")

        # 4. Check dependencies of other BLOCKED instances
        # Get all active instances associated with the current narrative
        active_instance_ids = await link_repo.get_instances_for_narrative(
            narrative_id,
            link_type=LinkType.ACTIVE
        )

        # Get history-associated instance_ids (for dependency checking)
        history_instance_ids = await link_repo.get_instances_for_narrative(
            narrative_id,
            link_type=LinkType.HISTORY
        )
        # Ensure the just-completed instance is in the history list
        if instance_id not in history_instance_ids:
            history_instance_ids.append(instance_id)

        newly_activated = []

        for inst_id in active_instance_ids:
            inst = await instance_repo.get_by_instance_id(inst_id)
            if not inst:
                continue

            # Check if it is in BLOCKED status
            inst_status = inst.status if isinstance(inst.status, str) else inst.status.value
            if inst_status != InstanceStatus.BLOCKED.value and inst_status != "blocked":
                continue

            # Check dependencies
            dependencies = inst.dependencies or []
            all_deps_completed = self._check_dependencies_from_db(
                dependencies=dependencies,
                active_ids=active_instance_ids,
                history_ids=history_instance_ids
            )

            if all_deps_completed:
                # 1. Activate instance
                await instance_repo.update_status(inst_id, InstanceStatus.ACTIVE)
                newly_activated.append(inst_id)
                logger.info(f"Activated blocked instance: {inst_id}")

                # 2. Let the module react (a task module reschedules its work)
                from narranexus.platform.module_system import module_registry

                module_class = module_registry.get(inst.module_class)
                if module_class is not None:
                    await module_class.on_instance_activated(inst_id, db_client)

        # 5. Update runtime cache (if narrative object was provided)
        if narrative:
            # Remove the completed instance from active_instances
            narrative.active_instances = [
                inst for inst in narrative.active_instances
                if inst.instance_id != instance_id
            ]
            # Add to history
            if instance_id not in narrative.instance_history_ids:
                narrative.instance_history_ids.append(instance_id)

            # Update status of newly activated instances
            for inst in narrative.active_instances:
                if inst.instance_id in newly_activated:
                    inst.status = InstanceStatus.ACTIVE

        logger.info(f"Newly activated: {newly_activated}")
        return newly_activated

    async def handle_completion_no_narrative(
        self,
        instance_id: str,
        new_status: "InstanceStatus",
    ) -> List[str]:
        """
        Narrative-independent counterpart of `handle_completion` (B-16).

        `handle_completion` resolves dependents by scanning
        `instance_narrative_links` scoped to a `narrative_id` — it can only
        ever see an instance that was linked to a narrative in the first
        place. Jobs created via `/api/jobs/complex` never bind one (the route
        never passes `narrative_id`), so a dependent Job's BLOCKED instance
        was invisible to `handle_completion` and stayed BLOCKED forever no
        matter how many of its dependencies completed — dependency chains
        "never trigger" (GitHub #114/#109).

        This resolves purely from `module_instances.dependencies` — the raw
        graph every instance already carries, independent of narrative
        linkage — scoped to this handler's `agent_id` (dependencies are
        instance_ids from the SAME job-complex batch, which is always
        single-agent).

        Semantics mirror `handle_completion`/`_check_dependencies_from_db`:
        a dependency counts as resolved once it reaches EITHER terminal
        state (COMPLETED or FAILED) — the caller, not this method, owns any
        "block on upstream failure" policy.
        """
        from narranexus.platform.schema.module_schema import InstanceStatus
        from narranexus.platform.repository import InstanceRepository

        db_client = await self._get_db_client()
        instance_repo = InstanceRepository(db_client)

        db_instance = await instance_repo.get_by_instance_id(instance_id)
        if not db_instance:
            logger.warning(f"Instance {instance_id} not found in database")
            return []

        now = datetime.now(timezone.utc)
        await instance_repo.update_status(
            instance_id=instance_id,
            status=new_status,
            completed_at=now if new_status in [InstanceStatus.COMPLETED, InstanceStatus.FAILED] else None,
        )

        terminal_statuses = {InstanceStatus.COMPLETED.value, InstanceStatus.FAILED.value}
        blocked = await instance_repo.get_by_agent(self.agent_id, status=InstanceStatus.BLOCKED)

        newly_activated: List[str] = []
        for inst in blocked:
            dependencies = inst.dependencies or []
            if instance_id not in dependencies:
                continue

            all_resolved = True
            for dep_id in dependencies:
                dep = await instance_repo.get_by_instance_id(dep_id)
                dep_status = dep.status if dep and isinstance(dep.status, str) else (
                    dep.status.value if dep else None
                )
                if dep_status not in terminal_statuses:
                    all_resolved = False
                    break
            if not all_resolved:
                continue

            await instance_repo.update_status(inst.instance_id, InstanceStatus.ACTIVE)
            newly_activated.append(inst.instance_id)
            logger.info(f"Activated blocked instance (no-narrative path): {inst.instance_id}")

            from narranexus.platform.module_system import module_registry

            module_class = module_registry.get(inst.module_class)
            if module_class is not None:
                await module_class.on_instance_activated(inst.instance_id, db_client)

        logger.info(f"Newly activated (no-narrative path): {newly_activated}")
        return newly_activated

    def _check_dependencies(
        self,
        dependencies: List[str],
        active_instances: List["ModuleInstance"],
        history_ids: List[str]
    ) -> bool:
        """
        Check if all dependencies are completed (legacy method, kept for compatibility)

        Args:
            dependencies: List of dependency instance_ids
            active_instances: Currently active instances
            history_ids: List of completed instance_ids

        Returns:
            bool: Whether all dependencies are completed
        """
        if not dependencies:
            return True

        for dep_id in dependencies:
            # Found in history means completed
            if dep_id in history_ids:
                continue

            # Still in active means not completed
            if any(inst.instance_id == dep_id for inst in active_instances):
                return False

            # Neither in history nor in active
            logger.warning(f"Dependency {dep_id} not found")
            return False

        return True

    def _check_dependencies_from_db(
        self,
        dependencies: List[str],
        active_ids: List[str],
        history_ids: List[str]
    ) -> bool:
        """
        Check if all dependencies are completed (using database ID lists)

        Args:
            dependencies: List of dependency instance_ids
            active_ids: List of currently active instance_ids
            history_ids: List of completed instance_ids

        Returns:
            bool: Whether all dependencies are completed
        """
        if not dependencies:
            return True

        for dep_id in dependencies:
            # Found in history means completed
            if dep_id in history_ids:
                continue

            # Still in active means not completed
            if dep_id in active_ids:
                return False

            # Neither in history nor in active
            logger.warning(f"Dependency {dep_id} not found in links")
            return False

        return True
