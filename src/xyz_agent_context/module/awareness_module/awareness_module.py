"""
@file_name: awareness.py
@author: NetMind.AI
@date: 2025-06-06
@description: This file is used to define the awareness of the agent.

Refactoring notes (2025-12-24):
- Use instance_awareness table to replace awareness table
- Data isolation through instance_id
- instance_id obtained from self.instance_id (passed in by ModuleLoader)
"""


from typing import Optional, List, Any
from mcp.server.fastmcp import FastMCP
from loguru import logger


# Module (same package)
from xyz_agent_context.module import XYZBaseModule, mcp_host

# Schema
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
)

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client

# Repository
from xyz_agent_context.repository import InstanceRepository, InstanceAwarenessRepository

# Prompts
from xyz_agent_context.module.awareness_module.prompts import AWARENESS_MODULE_INSTRUCTIONS


# Where a rename records itself inside the Awareness profile. The profile is
# injected verbatim into the system prompt every turn, so this is the one place
# a correction is guaranteed to be read.
#
# Why a rename needs a memory write at all (P1 段02 ①, prod evt_1f9c6680): the
# user named their first agent 「凑企鹅」, then handed that name to a SECOND
# agent. The rename tool wrote `agents.agent_name` and nothing else — but an
# agent's sense of who it is lives in free-text long-term memory, which still
# said 「凑企鹅 is actually my own agent name」. One column cannot correct
# thousands of words of narrative, so the rename leaves an explicit, dated
# retraction that retrieval and the injected profile both surface.
IDENTITY_CHANGE_SECTION = "## Identity Changes (platform record)"

# Keep the section bounded: renames are rare, but an unbounded log would eat
# the context window it lives in. Newest entries win.
MAX_IDENTITY_CHANGE_ENTRIES = 5


def build_identity_change_note(
    old_name: str, new_name: str, when: Optional[str] = None
) -> str:
    """One line recording a rename, written for the agent to read about itself.

    States both names and explicitly RETIRES the old one: the failure mode is
    memory that keeps asserting the previous identity, and "you are now X" does
    not contradict "I am Y" as far as a model is concerned — especially when
    the old name may now belong to a different agent of the same owner.
    """
    if when is None:
        from datetime import datetime, timezone
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"- {when}: renamed by your creator from 「{old_name}」 to 「{new_name}」. "
        f"You are 「{new_name}」. 「{old_name}」 is no longer your name — if it "
        f"appears in your memories or past conversations, that is history, and "
        f"it may now belong to a different agent."
    )


def merge_identity_change_note(profile: str, note: str) -> str:
    """Append ``note`` to the profile's identity-change section.

    Appends — never rewrites the rest of the profile (the agent's observations
    about its owner are not ours to edit, and losing them to a rename would be
    a worse bug than the one this fixes). Keeps a single section and the last
    ``MAX_IDENTITY_CHANGE_ENTRIES`` lines.
    """
    body = (profile or "").rstrip()
    if IDENTITY_CHANGE_SECTION in body:
        head, _, section = body.partition(IDENTITY_CHANGE_SECTION)
        entries = [
            ln.strip() for ln in section.splitlines() if ln.strip().startswith("- ")
        ]
        head = head.rstrip()
    else:
        head, entries = body, []

    entries.append(note)
    entries = entries[-MAX_IDENTITY_CHANGE_ENTRIES:]

    rebuilt = f"{IDENTITY_CHANGE_SECTION}\n" + "\n".join(entries) + "\n"
    return f"{head}\n\n{rebuilt}" if head else rebuilt


class AwarenessModule(XYZBaseModule):
    """
    Awareness Module
    """
    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        
        self.instructions = AWARENESS_MODULE_INSTRUCTIONS 
        self.port = 7801

    def get_config(self) -> ModuleConfig:
        """
        """
        return ModuleConfig(
            name="AwarenessModule",
            priority=3,
            enabled=True,
            description="Provides awareness and perception capabilities"
        )
        
    # ============================================================================= Hooks

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Get awareness data from the instance_awareness table.

        Refactoring notes:
        - Use self.instance_id to query the instance_awareness table
        - If self.instance_id is None, find instance through agent_id + module_class
        - If no record exists, create a default record
        - Use InstanceAwarenessRepository for data access
        """
        logger.debug(f"          → AwarenessModule.data_gathering() started for agent_id={self.agent_id}")
        default_awareness = "(You are a helpful assistant. You do not have any special abilities. Please try to ask the user to update your awareness.)"

        # Get instance_id
        instance_id = await self._get_instance_id()
        if not instance_id:
            logger.warning("            No instance_id found, using default awareness")
            ctx_data.awareness = default_awareness
            return ctx_data

        # Query using InstanceAwarenessRepository
        logger.debug(f"            Querying instance_awareness for instance_id={instance_id}")
        awareness_repo = InstanceAwarenessRepository(self.db)
        awareness_entity = await awareness_repo.get_by_instance(instance_id)

        if not awareness_entity:
            # If no record exists, create a default record
            logger.debug("            No awareness record found, creating default record")
            await awareness_repo.upsert(instance_id, default_awareness)
            awareness = default_awareness
            logger.debug(f"            Default awareness created: {awareness[:50]}...")
        else:
            # Extract the value of the "awareness" field
            awareness = awareness_entity.awareness
            logger.debug(f"            Awareness loaded from DB: {awareness[:50]}...")

        # Assign awareness string to ctx_data
        ctx_data.awareness = awareness
        logger.debug("          AwarenessModule.data_gathering() completed")

        return ctx_data

    async def _get_instance_id(self) -> Optional[str]:
        """
        Get the current Module's instance_id

        Prioritizes self.instance_id; if None, looks up through agent_id + module_class.
        AwarenessModule is an Agent-level module (is_public=1), each Agent has only one instance.
        """
        if self.instance_id:
            return self.instance_id

        # Look up through agent_id + module_class
        try:
            instance_repo = InstanceRepository(self.db)
            instances = await instance_repo.get_by_agent(
                agent_id=self.agent_id,
                module_class="AwarenessModule"
            )
            if instances:
                self.instance_id = instances[0].instance_id
                return self.instance_id
        except Exception as e:
            logger.warning(f"Failed to get instance_id: {e}")

        return None


    # ============================================================================= MCP Server
    
    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        """
        return MCPServerConfig(
            server_name="awareness_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse"
        )
        
    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server, providing the update_awareness tool

        Refactoring notes:
        - Use instance_awareness table to replace awareness table
        - Data isolation through instance_id
        """

        mcp = FastMCP("awareness_module")
        mcp.settings.port = self.port

        @mcp.tool()
        async def update_awareness(agent_id: str, new_awareness: str) -> str:
            """
            Update the agent's awareness profile with user preferences.

            ## When to Update

            **Immediately** when user:
            - Gives explicit preference: "Please always...", "I prefer...", "Don't..."
            - Provides feedback: "That was too long", "I liked that format"
            - Defines agent role: "You are my...", "Your job is to..."
            - Expresses style preference: "Be more concise", "Use technical terms"

            **After pattern observation (2-3 occurrences)** for:
            - Topic switching patterns (focused work vs. multi-tasking)
            - Task handling preferences (atomic steps vs. holistic)
            - Response format engagement (lists vs. paragraphs)

            **Do NOT update** for:
            - One-time task instructions
            - Temporary/situational requests

            ## Required Format

            Provide COMPLETE Markdown profile:

            ```markdown
            # Agent Awareness Profile

            ## 1. Narrative Management Preferences (Topic Organization)
            ### Topic Continuity Style
            - [observations]
            ### Topic Transition Preferences
            - [observations]
            ### Long-term Project Organization
            - [observations]

            ---

            ## 2. Task Decomposition Preferences (Work Style)
            ### Task Granularity
            - [observations]
            ### Tool Usage Patterns
            - [observations]
            ### Proactivity Level
            - [observations]
            ### Background Task Preferences
            - [observations]

            ---

            ## 3. Communication Style Preferences (Interaction)
            ### Tone and Voice
            - [observations]
            ### Response Format
            - [observations]
            ### Explanation Depth
            - [observations]
            ### Language Preferences
            - [observations]

            ---

            ## 4. Role and Identity
            ### Role Definition
            - [definition]
            ### Capability Boundaries
            - [boundaries]
            ### Behavioral Principles
            - [principles]
            ```

            ## Merge Strategy
            1. Preserve existing valid preferences
            2. Add new observations under appropriate sections
            3. Update/remove outdated preferences if user changes mind
            4. Always include all four sections

            Args:
                agent_id: Agent's unique identifier
                new_awareness: Complete awareness profile in Markdown format

            Returns:
                Success or error message
            """
            # Use MCP-dedicated database connection
            db = await AwarenessModule.get_mcp_db_client()

            # Find instance_id through agent_id + module_class
            from xyz_agent_context.repository import InstanceRepository, InstanceAwarenessRepository
            instance_repo = InstanceRepository(db)
            instances = await instance_repo.get_by_agent(
                agent_id=agent_id,
                module_class="AwarenessModule"
            )

            if not instances:
                return f"Error: No AwarenessModule instance found for agent_id={agent_id}"

            instance_id = instances[0].instance_id

            # Use InstanceAwarenessRepository to update awareness
            awareness_repo = InstanceAwarenessRepository(db)
            await awareness_repo.upsert(instance_id, new_awareness)
            return "Awareness updated successfully"

        @mcp.tool()
        async def update_agent_profile(
            agent_id: str,
            new_name: Optional[str] = None,
            new_description: Optional[str] = None,
        ) -> str:
            """
            Record who you are: your display name and/or your one-line description.

            Call this during bootstrap once your creator has told you what you
            are for — and again whenever that changes.

            **Your description is read by OTHER AGENTS, not by humans.** It is
            how a peer decides whether to route a question to you ("who can
            review a lesson plan?"). Write one plain line saying what you do and
            what to ask you for. Do not write marketing copy, and do not leave
            it empty: an agent with no description cannot be found by the peers
            who need it, and its owner's requests to "go ask X" fail.

            Renaming also files a dated correction into your Awareness profile,
            because your memories of being called something else do not update
            themselves — and the old name may now belong to a different agent.

            Args:
                agent_id: Agent's unique identifier
                new_name: New display name (omit to leave the name unchanged)
                new_description: New one-line description for peers (omit to
                    leave the description unchanged)

            Returns:
                Success or error message. Read it: it also tells you when the
                name you chose is already in use by another of your owner's
                agents.
            """
            if new_name is None and new_description is None:
                return (
                    "Error: nothing to update — pass new_name and/or "
                    "new_description."
                )

            db = await AwarenessModule.get_mcp_db_client()

            from xyz_agent_context.repository import AgentRepository
            repo = AgentRepository(db)

            agent = await repo.get_agent(agent_id)
            if not agent:
                return f"Error: Agent {agent_id} not found"

            updates: dict = {}
            old_name = (agent.agent_name or "").strip()
            renamed_from: Optional[str] = None
            notes: List[str] = []

            if new_name is not None:
                wanted = new_name.strip()
                if not wanted:
                    return "Error: new_name cannot be empty"
                if wanted != old_name:
                    updates["agent_name"] = wanted
                    renamed_from = old_name
                    # Duplicate names are ALLOWED — the owner may deliberately
                    # hand a name from one agent to another. What is forbidden
                    # is doing it silently: two agents answering to one name is
                    # exactly how the incident started, so name the current
                    # holder and let the agent check with its owner.
                    clash = await AwarenessModule._same_owner_name_holder(
                        db, owner_user_id=agent.created_by,
                        name=wanted, exclude_agent_id=agent_id,
                    )
                    if clash:
                        notes.append(
                            f"Note: 「{wanted}」 is currently also the name of "
                            f"{clash}, another agent of your owner. The rename "
                            f"was applied as asked — if that was not intended, "
                            f"ask your creator which agent should keep it."
                        )

            if new_description is not None:
                updates["agent_description"] = new_description.strip()

            if not updates:
                return (
                    "No changes needed — the values you passed already match "
                    "your current profile."
                )

            affected = await repo.update_agent(agent_id, updates)
            if affected <= 0:
                return "Error: the update did not apply; nothing was changed"

            # A rename is not complete until the memory that asserts the old
            # identity has been corrected (P1 段02 ①).
            if renamed_from:
                await AwarenessModule._record_identity_change(
                    db, agent_id, renamed_from, updates["agent_name"]
                )

            # Peers must see this now, not after the next turn (P1 段02 target 2).
            try:
                from xyz_agent_context.services.agent_discovery_sync import (
                    sync_agent_discovery,
                )
                await sync_agent_discovery(db, agent_id)
            except Exception as e:  # noqa: BLE001 — profile write already landed
                logger.warning(f"update_agent_profile: discovery sync failed: {e}")

            changed = ", ".join(sorted(updates))
            return " ".join([f"Profile updated successfully ({changed})."] + notes)

        return mcp

    # ============================================================================= Identity helpers

    @staticmethod
    async def _same_owner_name_holder(
        db, *, owner_user_id: str, name: str, exclude_agent_id: str
    ) -> Optional[str]:
        """agent_id of another agent of the SAME owner already using ``name``.

        Scoped to the owner on purpose: two users naming their agents the same
        thing is not a conflict and must never be reported across accounts.
        """
        try:
            rows = await db.get("agents", {"created_by": owner_user_id})
            for row in rows or []:
                if row.get("agent_id") == exclude_agent_id:
                    continue
                if (row.get("agent_name") or "").strip() == name:
                    return row.get("agent_id")
        except Exception as e:  # noqa: BLE001 — advisory note, never blocking
            logger.debug(f"name-clash check failed for {owner_user_id}: {e}")
        return None

    @staticmethod
    async def _record_identity_change(
        db, agent_id: str, old_name: str, new_name: str
    ) -> None:
        """File the rename into the agent's Awareness profile.

        Best-effort by design: the name change itself has already been written,
        and failing the tool afterwards would tell the model the rename did not
        happen. A missing note degrades to the old (buggy) behaviour, which is
        strictly better than reporting a false failure.
        """
        try:
            instances = await InstanceRepository(db).get_by_agent(
                agent_id=agent_id, module_class="AwarenessModule"
            )
            if not instances:
                logger.warning(
                    f"_record_identity_change: no AwarenessModule instance for "
                    f"{agent_id}; identity memory not corrected"
                )
                return
            instance_id = instances[0].instance_id
            awareness_repo = InstanceAwarenessRepository(db)
            current = await awareness_repo.get_by_instance(instance_id)
            profile = (current.awareness if current else "") or ""
            await awareness_repo.upsert(
                instance_id,
                merge_identity_change_note(
                    profile, build_identity_change_note(old_name, new_name)
                ),
            )
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.warning(f"_record_identity_change failed for {agent_id}: {e}")
            
    
    # ============================================================================= Database

    async def init_database_tables(self):
        """
        Initialize the instance_awareness table

        Table structure is managed by create_instance_awareness_table.py
        """
        db = await get_db_client()
        await db.create_table("""
            CREATE TABLE IF NOT EXISTS instance_awareness (
                id INT AUTO_INCREMENT PRIMARY KEY,
                instance_id VARCHAR(64) NOT NULL UNIQUE,
                awareness TEXT NOT NULL,
                created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
            )
        """)
        
    
if __name__ == "__main__":
    
    import asyncio
    asyncio.run(AwarenessModule("test_agent_id").init_database_tables())
    